import { createFileRoute } from '@tanstack/react-router';
import { CustomEventsLibrary } from '@/components/events/custom-events-library';

export const Route = createFileRoute(
  '/_app/$organizationId/$projectId/events/_tabs/custom'
)({
  component: CustomEventsPage,
});

function CustomEventsPage() {
  const { projectId } = Route.useParams();
  return <CustomEventsLibrary projectId={projectId} />;
}
