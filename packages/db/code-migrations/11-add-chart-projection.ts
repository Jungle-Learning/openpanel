import {
  chMigrationClient,
  runClickhouseMigrationCommands,
} from '../src/clickhouse/migration';
import { getIsCluster } from './helpers';

const projectionName = 'chart_events_by_name';

export async function up() {
  const isClustered = getIsCluster();
  const eventTable = isClustered ? 'events_replicated' : 'events';
  const onCluster = isClustered ? " ON CLUSTER '{cluster}'" : '';

  await runClickhouseMigrationCommands([
    `ALTER TABLE ${eventTable}${onCluster} ADD COLUMN IF NOT EXISTS _property_platform LowCardinality(String) MATERIALIZED properties['platform']`,
    `ALTER TABLE ${eventTable}${onCluster} ADD COLUMN IF NOT EXISTS _property_where_did_user_come_from LowCardinality(String) MATERIALIZED properties['whereDidUserComeFrom']`,
    `ALTER TABLE ${eventTable}${onCluster} ADD COLUMN IF NOT EXISTS _property_fixed_follow_up_question_selected_text LowCardinality(String) MATERIALIZED properties['fixedFollowUpQuestionSelectedText']`,
    `ALTER TABLE ${eventTable}${onCluster} ADD COLUMN IF NOT EXISTS _property_ai_generated_follow_up_question_selected_text LowCardinality(String) MATERIALIZED properties['aiGeneratedFollowUpQuestionSelectedText']`,
    `ALTER TABLE ${eventTable}${onCluster} ADD PROJECTION IF NOT EXISTS ${projectionName} (
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
  ]);

  if (isClustered) {
    return;
  }

  // Projection materialization is deliberately limited to the rolling year
  // used by the dashboard. This keeps the migration bounded on large
  // self-hosted installations while new parts populate automatically.
  const partitionsResponse = await chMigrationClient.query({
    query: `SELECT DISTINCT partition
      FROM system.parts
      WHERE active
        AND database = currentDatabase()
        AND table = 'events'
        AND partition >= toString(toYYYYMM(now() - INTERVAL 12 MONTH))
        AND partition <= toString(toYYYYMM(now()))
        AND partition NOT IN (
          SELECT DISTINCT partition
          FROM system.projection_parts
          WHERE active
            AND database = currentDatabase()
            AND table = 'events'
            AND name = '${projectionName}'
        )
      ORDER BY partition`,
    format: 'JSONEachRow',
  });
  const partitions = await partitionsResponse.json<{ partition: string }>();

  await runClickhouseMigrationCommands(
    partitions.map(
      ({ partition }) =>
        `ALTER TABLE events MATERIALIZE PROJECTION ${projectionName} IN PARTITION ${partition}`
    )
  );
}
