import { describe, expect, it } from 'vitest';
import { buildFilterWhere } from './filter-where.service';

describe('buildFilterWhere', () => {
  it('filters sessions to only rows with replay chunks', () => {
    const where = buildFilterWhere(
      [
        {
          name: 'session.has_replay',
          operator: 'is',
          value: [true],
        },
      ],
      'project-1',
      {
        selfTable: 'sessions',
        profileIdExpr: 'profile_id',
      },
    );

    expect(where.f0).toContain('id IN');
    expect(where.f0).toContain('session_replay_chunks');
    expect(where.f0).toContain("project_id = 'project-1'");
  });

  it('filters sessions to rows without replay chunks when negated', () => {
    const where = buildFilterWhere(
      [
        {
          name: 'session.has_replay',
          operator: 'isNot',
          value: [true],
        },
      ],
      'project-1',
      {
        selfTable: 'sessions',
        profileIdExpr: 'profile_id',
      },
    );

    expect(where.f0).toContain('id NOT IN');
    expect(where.f0).toContain('session_replay_chunks');
  });
});
