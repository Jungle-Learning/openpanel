import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  ArrowRightIcon,
  CheckIcon,
  GanttChartIcon,
  InfoIcon,
  PencilIcon,
  XIcon,
} from 'lucide-react';
import { type ReactElement, useId, useState } from 'react';
import { toast } from 'sonner';
import {
  type CustomEvent,
  CustomEventBadge,
  CustomEventDefinition,
  getCustomEventCountingDescription,
} from './custom-event-definition';
import { Button, LinkButton } from '@/components/ui/button';
import { ComboboxEvents } from '@/components/ui/combobox-events';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from '@/components/ui/dialog';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Textarea } from '@/components/ui/textarea';
import { useAppParams } from '@/hooks/use-app-params';
import { useTRPC } from '@/integrations/trpc/react';
import { cn } from '@/utils/cn';

export function CustomEventDialog({
  projectId,
  customEventId,
  children,
  defaultEditing = false,
}: {
  projectId: string;
  customEventId: string;
  children: ReactElement;
  defaultEditing?: boolean;
}) {
  const [open, setOpen] = useState(false);
  return (
    <Dialog onOpenChange={setOpen} open={open}>
      <DialogTrigger asChild>{children}</DialogTrigger>
      {open && (
        <DialogContent
          className="flex max-h-[90dvh] flex-col gap-0 overflow-hidden bg-card p-0 sm:max-w-xl"
          showCloseButton
        >
          <DefinitionDialogContent
            customEventId={customEventId}
            defaultEditing={defaultEditing}
            projectId={projectId}
          />
        </DialogContent>
      )}
    </Dialog>
  );
}

function DefinitionDialogContent({
  projectId,
  customEventId,
  defaultEditing,
}: {
  projectId: string;
  customEventId: string;
  defaultEditing: boolean;
}) {
  const trpc = useTRPC();
  const [editing, setEditing] = useState(defaultEditing);
  const { organizationId } = useAppParams();
  const query = useQuery(
    trpc.event.customEvent.queryOptions({ projectId, id: customEventId })
  );

  if (!query.data) {
    return (
      <>
        <DialogHeader className="shrink-0 border-b p-6 text-left">
          <DialogTitle>Custom event definition</DialogTitle>
          <DialogDescription>
            {query.isError
              ? 'The definition could not be loaded.'
              : 'Loading the saved definition…'}
          </DialogDescription>
        </DialogHeader>
        <div className="p-6" role="status">
          {query.isError ? (
            <div className="space-y-4">
              <p className="text-muted-foreground text-sm">
                {query.error.message}
              </p>
              <Button onClick={() => query.refetch()} variant="outline">
                Try again
              </Button>
            </div>
          ) : (
            <p className="text-muted-foreground text-sm">Loading…</p>
          )}
        </div>
      </>
    );
  }
  const event = query.data;
  return (
    <>
      <DialogHeader className="shrink-0 border-b p-6 text-left">
        <CustomEventBadge />
        <DialogTitle className="mt-2 break-words pr-5 text-2xl leading-tight">
          {editing ? 'Edit definition' : event.name}
        </DialogTitle>
        <DialogDescription className="break-words leading-relaxed">
          {editing
            ? 'Changes apply everywhere this custom event is used.'
            : event.description || 'No description added.'}
        </DialogDescription>
      </DialogHeader>
      {editing ? (
        <CustomEventEditor
          event={event}
          onCancel={() => setEditing(false)}
          onSaved={() => setEditing(false)}
        />
      ) : (
        <>
          <div className="min-h-0 overflow-y-auto p-6">
            <CustomEventDefinition event={event} />
          </div>
          <div className="shrink-0 space-y-3 border-t p-6">
            <DialogFooter className="gap-3 sm:justify-between">
              <LinkButton
                params={{ organizationId, projectId }}
                to="/$organizationId/$projectId/events/custom"
                variant="link"
              >
                Manage custom events
                <ArrowRightIcon className="ml-1" size={14} />
              </LinkButton>
              <Button icon={PencilIcon} onClick={() => setEditing(true)}>
                Edit definition
              </Button>
            </DialogFooter>
            <p className="text-muted-foreground text-sm leading-relaxed">
              Editing updates every report that uses this definition, including
              past dates.
            </p>
          </div>
        </>
      )}
    </>
  );
}

function CustomEventEditor({
  event,
  onCancel,
  onSaved,
}: {
  event: CustomEvent;
  onCancel: () => void;
  onSaved: () => void;
}) {
  const formId = useId();
  const [name, setName] = useState(event.name);
  const [description, setDescription] = useState(event.description ?? '');
  const [operation, setOperation] = useState(event.operation);
  const [eventNames, setEventNames] = useState(event.eventNames);
  const trpc = useTRPC();
  const queryClient = useQueryClient();
  const sources = useQuery(
    trpc.chart.events.queryOptions({
      projectId: event.projectId,
      includeCustomEvents: false,
    })
  );
  const mutation = useMutation(
    trpc.event.updateCustomEvent.mutationOptions({
      onSuccess(updated) {
        queryClient.setQueryData(
          trpc.event.customEvent.queryKey({
            projectId: event.projectId,
            id: event.id,
          }),
          updated
        );
        queryClient.invalidateQueries(
          trpc.event.customEvents.queryFilter({ projectId: event.projectId })
        );
        queryClient.invalidateQueries(trpc.chart.pathFilter());
        toast('Custom event updated');
        onSaved();
      },
    })
  );
  const available = (sources.data ?? []).filter(
    (source) => source.name !== '*' && !eventNames.includes(source.name)
  );

  return (
    <>
      <form
        className="min-h-0 space-y-5 overflow-y-auto p-6"
        id={formId}
        onSubmit={(submitEvent) => {
          submitEvent.preventDefault();
          if (!name.trim() || mutation.isPending) {
            return;
          }
          mutation.mutate({
            id: event.id,
            projectId: event.projectId,
            updatedAt: event.updatedAt,
            name: name.trim(),
            description: description.trim(),
            operation,
            eventNames,
          });
        }}
      >
        <fieldset
          className="space-y-5 disabled:opacity-70"
          disabled={mutation.isPending}
        >
          <div>
            <Label htmlFor={`${formId}-name`}>Name</Label>
            <Input
              autoFocus
              id={`${formId}-name`}
              maxLength={80}
              onChange={(e) => setName(e.target.value)}
              required
              value={name}
            />
          </div>
          <div>
            <Label htmlFor={`${formId}-description`}>
              Description (optional)
            </Label>
            <Textarea
              className="min-h-20 leading-relaxed"
              id={`${formId}-description`}
              maxLength={240}
              onChange={(e) => setDescription(e.target.value)}
              value={description}
            />
          </div>
          <div>
            <p className="mb-2 font-medium" id={`${formId}-logic`}>
              Matching logic
            </p>
            <div
              aria-labelledby={`${formId}-logic`}
              className="grid grid-cols-2 gap-1 rounded-lg bg-def-100 p-1"
              role="group"
            >
              {(['union', 'intersection'] as const).map((value) => (
                <button
                  aria-pressed={operation === value}
                  className={cn(
                    'flex items-center justify-center gap-2 rounded-md px-2 py-2 text-muted-foreground text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring',
                    operation === value &&
                      'bg-card font-medium text-foreground shadow-xs'
                  )}
                  key={value}
                  onClick={() => setOperation(value)}
                  type="button"
                >
                  <span className="text-xs">
                    {value === 'union' ? 'OR' : 'AND'}
                  </span>
                  {value === 'union'
                    ? 'Any of these events'
                    : 'All of these events'}
                </button>
              ))}
            </div>
            <p className="mt-2 text-muted-foreground text-sm leading-relaxed">
              {getCustomEventCountingDescription(operation)}
            </p>
          </div>
          <div>
            <p className="mb-3 font-medium">
              Source events{' '}
              <span className="font-normal text-muted-foreground">
                ({eventNames.length}/10)
              </span>
            </p>
            <ul className="divide-y overflow-hidden rounded-lg border bg-card">
              {eventNames.map((source) => (
                <li
                  className="flex min-w-0 items-center gap-3 px-3 py-2"
                  key={source}
                >
                  <GanttChartIcon
                    className="shrink-0 text-muted-foreground"
                    size={15}
                  />
                  <code className="min-w-0 flex-1 break-words text-sm">
                    {source}
                  </code>
                  <Button
                    aria-label={`Remove ${source}`}
                    disabled={eventNames.length <= 2}
                    onClick={() =>
                      setEventNames((current) =>
                        current.filter((item) => item !== source)
                      )
                    }
                    size="icon"
                    title={
                      eventNames.length <= 2
                        ? 'At least two source events are required'
                        : `Remove ${source}`
                    }
                    variant="ghost"
                  >
                    <XIcon size={14} />
                  </Button>
                </li>
              ))}
            </ul>
            <div className="mt-3">
              <ComboboxEvents
                className="w-full"
                disabled={
                  sources.isPending ||
                  sources.isError ||
                  eventNames.length >= 10 ||
                  available.length === 0 ||
                  mutation.isPending
                }
                items={available}
                onChange={(source) =>
                  setEventNames((current) =>
                    current.length < 10 && !current.includes(source)
                      ? [...current, source]
                      : current
                  )
                }
                placeholder={
                  sources.isPending
                    ? 'Loading source events…'
                    : available.length === 0 && !sources.isError
                      ? 'No more source events'
                      : 'Add source event'
                }
                searchable
                value=""
              />
            </div>
            {sources.isError ? (
              <p className="mt-2 text-destructive text-sm">
                Could not load source events.{' '}
                <button
                  className="underline"
                  onClick={() => sources.refetch()}
                  type="button"
                >
                  Try again
                </button>
              </p>
            ) : (
              <p className="mt-2 text-muted-foreground text-sm">
                Choose 2–10 tracked events.
              </p>
            )}
          </div>
        </fieldset>
        {mutation.isError && (
          <div
            className="space-y-2 rounded-md border border-destructive/30 p-3 text-destructive text-sm leading-relaxed"
            role="alert"
          >
            <p>{mutation.error.message}</p>
            {mutation.error.data?.code === 'CONFLICT' && (
              <Button
                onClick={() => {
                  queryClient.invalidateQueries(
                    trpc.event.customEvent.queryFilter({
                      projectId: event.projectId,
                      id: event.id,
                    })
                  );
                  onCancel();
                }}
                variant="outline"
              >
                Reload definition
              </Button>
            )}
          </div>
        )}
      </form>
      <div className="shrink-0 space-y-3 border-t bg-card p-6">
        <DialogFooter className="sm:justify-between">
          <Button
            disabled={mutation.isPending}
            onClick={onCancel}
            variant="outline"
          >
            Cancel
          </Button>
          <Button
            disabled={!name.trim()}
            form={formId}
            icon={CheckIcon}
            loading={mutation.isPending}
            type="submit"
          >
            Save changes
          </Button>
        </DialogFooter>
        <p className="flex gap-2 text-muted-foreground text-sm leading-relaxed">
          <InfoIcon className="mt-0.5 shrink-0" size={14} />
          Changes affect all reports using this event, including past dates.
        </p>
      </div>
    </>
  );
}
