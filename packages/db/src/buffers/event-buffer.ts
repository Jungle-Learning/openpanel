import { getRedisCache, publishEvent } from '@openpanel/redis';
import { ch, chQuery } from '../clickhouse/client';
import type { IClickhouseEvent } from '../services/event.service';
import { BaseBuffer } from './base-buffer';

const PROJECT_ID_NEEDLE = '"project_id":"';
const CREATED_AT_NEEDLE = '"created_at":"';
const UNKNOWN_CREATED_AT_DATE = '\uffff';
const MAX_DAILY_PARTITIONS_PER_INSERT = 90;

/**
 * Extract the top-level `project_id` from a single JSONEachRow event
 * line without doing a full JSON.parse.
 *
 * Fast path: scan with `indexOf`. If the needle appears exactly once,
 * we know it's the top-level field (any `project_id` nested in a JSON
 * string value would be escaped as `\"project_id\":\"...`, which the
 * indexOf scan can't match because of the `\` in front).
 *
 * Slow path: if the needle appears two or more times, the line has a
 * legitimate nested `project_id` key (e.g. inside `properties` if a
 * user happens to set one with that name). The regex/indexOf can't
 * tell which is the top-level one — at that point we fall back to a
 * real JSON.parse for correctness. This is rare in practice but
 * removes the silent-attribution failure mode entirely.
 *
 * Returns `null` if no top-level project_id is present or the row is
 * malformed.
 */
export function extractProjectId(line: string): string | null {
  const first = line.indexOf(PROJECT_ID_NEEDLE);
  if (first < 0) {
    return null;
  }

  const second = line.indexOf(
    PROJECT_ID_NEEDLE,
    first + PROJECT_ID_NEEDLE.length
  );
  if (second >= 0) {
    try {
      const obj = JSON.parse(line) as { project_id?: unknown };
      return typeof obj.project_id === 'string' ? obj.project_id : null;
    } catch {
      return null;
    }
  }

  const valueStart = first + PROJECT_ID_NEEDLE.length;
  const valueEnd = line.indexOf('"', valueStart);
  // valueEnd === valueStart means the value is empty (`"project_id":""`)
  // — treat as missing so we don't pollute pub/sub counts with an empty
  // key. Matches the old regex's `[^"]+` (one-or-more) behavior.
  if (valueEnd <= valueStart) {
    return null;
  }
  return line.slice(valueStart, valueEnd);
}

/**
 * Extract the top-level event date used by the daily-partitioned materialized
 * views. The common path avoids parsing the full event; duplicate field names
 * fall back to JSON.parse so a user property cannot override the event date.
 */
export function extractCreatedAtDate(line: string): string | null {
  const first = line.indexOf(CREATED_AT_NEEDLE);
  if (first < 0) {
    return null;
  }

  const second = line.indexOf(
    CREATED_AT_NEEDLE,
    first + CREATED_AT_NEEDLE.length
  );
  if (second >= 0) {
    try {
      const obj = JSON.parse(line) as { created_at?: unknown };
      if (typeof obj.created_at !== 'string') {
        return null;
      }
      const date = obj.created_at.slice(0, 10);
      return /^\d{4}-\d{2}-\d{2}$/.test(date) ? date : null;
    } catch {
      return null;
    }
  }

  const valueStart = first + CREATED_AT_NEEDLE.length;
  const date = line.slice(valueStart, valueStart + 10);
  return /^\d{4}-\d{2}-\d{2}$/.test(date) ? date : null;
}

/**
 * ClickHouse's `dau_mv` is partitioned by day and rejects a single insert
 * block spanning more than 100 partitions. Historical events can arrive in
 * user order rather than timestamp order, so normal row-count chunking is not
 * sufficient. Contiguous chunks preserve Redis order so each successful
 * insert can be acknowledged immediately without replaying earlier chunks.
 */
export function getPartitionSafeEventChunks(
  lines: string[],
  maxRowsPerChunk: number,
  maxDatesPerChunk = MAX_DAILY_PARTITIONS_PER_INSERT
): string[][] {
  const chunks: string[][] = [];
  let currentChunk: string[] = [];
  let currentDates = new Set<string>();
  const flushCurrentChunk = () => {
    if (currentChunk.length === 0) {
      return;
    }
    chunks.push(currentChunk);
    currentChunk = [];
    currentDates = new Set<string>();
  };

  for (const line of lines) {
    const date = extractCreatedAtDate(line) ?? UNKNOWN_CREATED_AT_DATE;
    const wouldExceedDateLimit =
      !currentDates.has(date) && currentDates.size >= maxDatesPerChunk;
    if (currentChunk.length >= maxRowsPerChunk || wouldExceedDateLimit) {
      flushCurrentChunk();
    }

    currentChunk.push(line);
    currentDates.add(date);
  }
  flushCurrentChunk();

  return chunks;
}

export class EventBuffer extends BaseBuffer {
  private readonly batchSize = process.env.EVENT_BUFFER_BATCH_SIZE
    ? Number.parseInt(process.env.EVENT_BUFFER_BATCH_SIZE, 10)
    : 4000;
  private readonly chunkSize = process.env.EVENT_BUFFER_CHUNK_SIZE
    ? Number.parseInt(process.env.EVENT_BUFFER_CHUNK_SIZE, 10)
    : 1000;

  private readonly microBatchIntervalMs = process.env
    .EVENT_BUFFER_MICRO_BATCH_MS
    ? Number.parseInt(process.env.EVENT_BUFFER_MICRO_BATCH_MS, 10)
    : 10;
  private readonly microBatchMaxSize = process.env.EVENT_BUFFER_MICRO_BATCH_SIZE
    ? Number.parseInt(process.env.EVENT_BUFFER_MICRO_BATCH_SIZE, 10)
    : 100;

  private pendingEvents: IClickhouseEvent[] = [];
  private flushTimer: ReturnType<typeof setTimeout> | null = null;
  private isFlushing = false;
  /** Tracks consecutive flush failures for observability; reset on success. */
  private flushRetryCount = 0;

  private readonly queueKey = 'event_buffer:queue';

  constructor() {
    super({
      name: 'event',
      onFlush: async () => {
        await this.processBuffer();
      },
    });
  }

  bulkAdd(events: IClickhouseEvent[]) {
    for (const event of events) {
      this.add(event);
    }
  }

  add(event: IClickhouseEvent) {
    // Event-buffer's add() is synchronous (in-memory push). Measured anyway
    // for consistency with the other buffers' add-latency tracking.
    const start = performance.now();
    this.pendingEvents.push(event);

    if (this.pendingEvents.length >= this.microBatchMaxSize) {
      this.flushLocalBuffer();
    } else if (!this.flushTimer) {
      this.flushTimer = setTimeout(() => {
        this.flushTimer = null;
        this.flushLocalBuffer();
      }, this.microBatchIntervalMs);
    }

    try {
      this.addObserver?.({
        buffer: this.name,
        durationMs: performance.now() - start,
      });
    } catch {
      // never break add on observer failure
    }
  }

  /** Number of events buffered locally in process memory, before Redis. */
  public getPendingLocalCount(): number {
    return this.pendingEvents.length;
  }

  public async flush() {
    if (this.flushTimer) {
      clearTimeout(this.flushTimer);
      this.flushTimer = null;
    }
    await this.flushLocalBuffer();
  }

  private async flushLocalBuffer() {
    if (this.isFlushing || this.pendingEvents.length === 0) {
      return;
    }

    this.isFlushing = true;

    const eventsToFlush = this.pendingEvents;
    this.pendingEvents = [];

    try {
      const redis = getRedisCache();
      const multi = redis.multi();

      for (const event of eventsToFlush) {
        multi.rpush(this.queueKey, JSON.stringify(event));
      }

      await multi.exec();

      this.flushRetryCount = 0;
    } catch (error) {
      // Re-queue failed events at the front to preserve order and avoid data loss
      this.pendingEvents = eventsToFlush.concat(this.pendingEvents);

      this.flushRetryCount += 1;
      this.logger.warn(
        {
          err: error,
          eventCount: eventsToFlush.length,
          flushRetryCount: this.flushRetryCount,
        },
        'Failed to flush local buffer to Redis; events re-queued'
      );
    } finally {
      this.isFlushing = false;
      // Events may have accumulated while we were flushing; schedule another flush if needed
      if (this.pendingEvents.length > 0 && !this.flushTimer) {
        this.flushTimer = setTimeout(() => {
          this.flushTimer = null;
          this.flushLocalBuffer();
        }, this.microBatchIntervalMs);
      }
    }
  }

  protected getRedisListKey(): string {
    return this.queueKey;
  }

  async processBuffer() {
    const redis = getRedisCache();

    const lrangeStart = performance.now();
    const queueEvents = await redis.lrange(
      this.queueKey,
      0,
      this.batchSize - 1
    );
    const lrangeMs = performance.now() - lrangeStart;

    if (queueEvents.length === 0) {
      this.reportFlushStats({ rowsProcessed: 0, phases: { lrangeMs } });
      return;
    }

    // We don't need to JSON.parse the events at all — they're already
    // valid JSONEachRow lines (one stringified event per Redis entry).
    // The client's custom `json.stringify` (set in CLICKHOUSE_OPTIONS)
    // passes strings through unchanged, so the bytes go straight from
    // Redis → CH HTTP body. This skips:
    //   - JSON.parse × N (50–300ms for N=100k)
    //   - The @clickhouse/client's internal JSON.stringify × N (same)
    //   - All the intermediate object allocations (saves ~200MB heap)
    //
    const partitionSafeChunks = getPartitionSafeEventChunks(
      queueEvents,
      this.chunkSize
    );
    let chInsertMs = 0;
    let trimMs = 0;
    let rowsProcessed = 0;
    try {
      for (const chunk of partitionSafeChunks) {
        this.assertFlushLockOwned();
        const countByProject = new Map<string, number>();
        for (const line of chunk) {
          const projectId = extractProjectId(line);
          if (projectId) {
            countByProject.set(
              projectId,
              (countByProject.get(projectId) ?? 0) + 1
            );
          }
        }

        const chStart = performance.now();
        await ch.insert({
          table: 'events',
          // Stream the raw JSONEachRow lines straight through — already
          // serialized in Redis, no client-side parse/stringify needed.
          values: this.jsonEachRowStream(chunk),
          format: 'JSONEachRow',
          clickhouse_settings: this.getClickhouseSettings(),
        });
        chInsertMs += performance.now() - chStart;

        const trimStart = performance.now();
        await this.trimRedisListWhileFlushLockOwned(
          this.queueKey,
          chunk.length,
          -1
        );
        trimMs += performance.now() - trimStart;
        rowsProcessed += chunk.length;

        for (const [projectId, count] of countByProject) {
          try {
            await publishEvent('events', 'batch', { projectId, count });
          } catch (error) {
            this.logger.warn(
              { err: error, projectId, count },
              'Failed to publish committed event batch'
            );
          }
        }
      }
    } finally {
      this.reportFlushStats({
        rowsProcessed,
        phases: { lrangeMs, chInsertMs, trimMs },
      });
    }

    await this.yieldToEventLoop();
  }

  public async getActiveVisitorCount(projectId: string): Promise<number> {
    const rows = await chQuery<{ count: number }>(
      `SELECT uniq(profile_id) AS count
       FROM events
       WHERE project_id = '${projectId}'
         AND profile_id != ''
         AND created_at >= now() - INTERVAL 5 MINUTE`
    );
    return rows[0]?.count ?? 0;
  }
}
