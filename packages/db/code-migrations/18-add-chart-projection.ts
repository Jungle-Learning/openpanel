import fs from 'node:fs';
import path from 'node:path';
import { TABLE_NAMES } from '../src/clickhouse/client';
import {
  addColumns,
  chMigrationClient,
  runClickhouseMigrationCommands,
} from '../src/clickhouse/migration';
import { getIsCluster } from './helpers';

const PROJECTION_NAME = 'chart_events_by_name';

export function getChartProjectionCommands(isClustered: boolean): string[] {
  const projectionTable = isClustered
    ? `${TABLE_NAMES.events}_replicated`
    : TABLE_NAMES.events;
  const onCluster = isClustered ? " ON CLUSTER '{cluster}'" : '';

  return [
    ...addColumns(
      TABLE_NAMES.events,
      [
        "`_property_platform` LowCardinality(String) MATERIALIZED properties['platform']",
        "`_property_where_did_user_come_from` LowCardinality(String) MATERIALIZED properties['whereDidUserComeFrom']",
        "`_property_fixed_follow_up_question_selected_text` LowCardinality(String) MATERIALIZED properties['fixedFollowUpQuestionSelectedText']",
        "`_property_ai_generated_follow_up_question_selected_text` LowCardinality(String) MATERIALIZED properties['aiGeneratedFollowUpQuestionSelectedText']",
      ],
      isClustered
    ),
    `ALTER TABLE ${projectionTable}${onCluster} ADD PROJECTION IF NOT EXISTS ${PROJECTION_NAME} (
      SELECT
        project_id,
        name,
        created_at,
        country,
        profile_id,
        session_id,
        device_id,
        os,
        _property_platform,
        _property_where_did_user_come_from,
        _property_fixed_follow_up_question_selected_text,
        _property_ai_generated_follow_up_question_selected_text
      ORDER BY (project_id, name, created_at)
    )`,
  ];
}

export async function up() {
  const isClustered = getIsCluster();
  const sqls = getChartProjectionCommands(isClustered);

  fs.writeFileSync(
    path.join(import.meta.filename.replace('.ts', '.sql')),
    sqls
      .map((sql) =>
        sql
          .trim()
          .replace(/;$/, '')
          .replace(/\n{2,}/g, '\n')
          .concat(';')
      )
      .join('\n\n---\n\n')
  );

  if (process.argv.includes('--dry')) {
    return;
  }

  await runClickhouseMigrationCommands(sqls);

  if (isClustered) {
    // New parts populate the projection automatically. Historical cluster
    // backfills should be scheduled per shard rather than making a general
    // schema migration fan out a large mutation across every replica.
    return;
  }

  // Materialize only the rolling year used by the dashboard. The columns
  // remain valid for older parts, while bounding migration time and storage.
  const partitionsResponse = await chMigrationClient.query({
    query: `SELECT DISTINCT partition
      FROM system.parts
      WHERE active
        AND database = currentDatabase()
        AND table = '${TABLE_NAMES.events}'
        AND partition >= toString(toYYYYMM(now() - INTERVAL 12 MONTH))
        AND partition <= toString(toYYYYMM(now()))
        AND partition NOT IN (
          SELECT DISTINCT partition
          FROM system.projection_parts
          WHERE active
            AND database = currentDatabase()
            AND table = '${TABLE_NAMES.events}'
            AND name = '${PROJECTION_NAME}'
        )
      ORDER BY partition`,
    format: 'JSONEachRow',
  });
  const partitions = await partitionsResponse.json<{ partition: string }>();
  const validPartitions = partitions.filter(({ partition }) =>
    /^\d{6}$/.test(partition)
  );

  await runClickhouseMigrationCommands(
    validPartitions.map(
      ({ partition }) =>
        `ALTER TABLE ${TABLE_NAMES.events} MATERIALIZE PROJECTION ${PROJECTION_NAME} IN PARTITION ${partition}`
    )
  );
}
