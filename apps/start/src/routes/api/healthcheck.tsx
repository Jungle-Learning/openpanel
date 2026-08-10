import { createFileRoute } from '@tanstack/react-router';

export const Route = createFileRoute('/api/healthcheck')({
  server: {
    handlers: {
      GET: async () => {
        return Response.json({
          ok: true,
          releaseSha: process.env.OPENPANEL_RELEASE_SHA ?? null,
        });
      },
    },
  },
});
