import type { ProfileSetOperation } from '@openpanel/validation';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { SaveIcon } from 'lucide-react';
import { useState } from 'react';
import { toast } from 'sonner';
import { Button } from '@/components/ui/button';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Textarea } from '@/components/ui/textarea';
import { handleError, useTRPC } from '@/integrations/trpc/react';

interface SaveCustomEventDialogProps {
  projectId: string;
  operation: ProfileSetOperation;
  eventNames: string[];
  suggestedName?: string;
}

export function SaveCustomEventDialog({
  projectId,
  operation,
  eventNames,
  suggestedName,
}: SaveCustomEventDialogProps) {
  const [open, setOpen] = useState(false);
  const [name, setName] = useState(suggestedName ?? '');
  const [description, setDescription] = useState('');
  const trpc = useTRPC();
  const queryClient = useQueryClient();
  const createCustomEvent = useMutation(
    trpc.event.createCustomEvent.mutationOptions({
      onSuccess() {
        queryClient.invalidateQueries(trpc.chart.events.pathFilter());
        toast('Custom event created', {
          description: `${name.trim()} is now available in the event picker.`,
        });
        setOpen(false);
      },
      onError: handleError,
    }),
  );

  return (
    <>
      <Button
        icon={SaveIcon}
        onClick={() => {
          if (!name.trim() && suggestedName?.trim()) {
            setName(suggestedName.trim());
          }
          setOpen(true);
        }}
        size="sm"
        type="button"
        variant="outline"
      >
        Save as custom event
      </Button>
      <Dialog onOpenChange={setOpen} open={open}>
        <DialogContent showCloseButton>
          <DialogHeader>
            <DialogTitle>Save custom event</DialogTitle>
            <DialogDescription>
              This creates a reusable query-time event. It does not emit or
              duplicate tracked events.
            </DialogDescription>
          </DialogHeader>
          <form
            className="flex flex-col gap-4"
            onSubmit={(submitEvent) => {
              submitEvent.preventDefault();
              const trimmedName = name.trim();
              if (!trimmedName) {
                return;
              }
              createCustomEvent.mutate({
                projectId,
                name: trimmedName,
                description: description.trim() || undefined,
                operation,
                eventNames,
              });
            }}
          >
            <div>
              <Label htmlFor="custom-event-name">Name</Label>
              <Input
                autoFocus
                id="custom-event-name"
                maxLength={80}
                onChange={(inputEvent) => setName(inputEvent.target.value)}
                placeholder="Active Action"
                value={name}
              />
            </div>
            <div>
              <Label htmlFor="custom-event-description">
                Description (optional)
              </Label>
              <Textarea
                id="custom-event-description"
                maxLength={240}
                onChange={(inputEvent) =>
                  setDescription(inputEvent.target.value)
                }
                placeholder="A user completed at least one meaningful learning action."
                value={description}
              />
            </div>
            <div className="rounded-md border bg-card p-3 text-sm">
              <div className="font-medium">
                {operation === 'union'
                  ? 'Any of these events'
                  : 'All of these events'}
              </div>
              <ul className="mt-2 list-disc space-y-1 pl-5 text-muted-foreground">
                {eventNames.map((eventName) => (
                  <li key={eventName}>{eventName}</li>
                ))}
              </ul>
            </div>
            <DialogFooter>
              <Button
                onClick={() => setOpen(false)}
                type="button"
                variant="outline"
              >
                Cancel
              </Button>
              <Button
                disabled={!name.trim()}
                loading={createCustomEvent.isPending}
                type="submit"
              >
                Create custom event
              </Button>
            </DialogFooter>
          </form>
        </DialogContent>
      </Dialog>
    </>
  );
}
