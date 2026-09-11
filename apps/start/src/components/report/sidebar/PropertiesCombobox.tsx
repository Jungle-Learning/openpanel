import type { IChartEvent } from '@openpanel/validation';
import { useQuery } from '@tanstack/react-query';
import { AnimatePresence, motion } from 'framer-motion';
import {
  ActivityIcon,
  ArrowLeftIcon,
  Building2Icon,
  DatabaseIcon,
  TargetIcon,
  UserIcon,
} from 'lucide-react';
import { type Dispatch, type SetStateAction, useEffect, useState } from 'react';
import { Button } from '@/components/ui/button';
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuGroup,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu';
import { Input } from '@/components/ui/input';
import { useAppParams } from '@/hooks/use-app-params';
import { useEventPropertyOptions } from '@/hooks/use-event-properties';
import { useProfileProperties } from '@/hooks/use-profile-properties';
import { useTRPC } from '@/integrations/trpc/react';
import {
  EVENT_PROPERTY_SECTIONS,
  getEventPropertySection,
} from './event-property-utils';
import { getProfilePropertyNames } from './profile-property-utils';

export type PropertiesComboboxAction = {
  value: string;
  label: string;
  description: string;
  section?: string;
};

export type PropertiesComboboxCategory =
  | 'event'
  | 'profile'
  | 'group'
  | 'cohort'
  | 'session';

type State = 'index' | Exclude<PropertiesComboboxCategory, 'cohort'>;

interface PropertiesComboboxProps {
  event?: IChartEvent;
  events?: string[];
  customEventIds?: string[];
  children: (setOpen: Dispatch<SetStateAction<boolean>>) => React.ReactNode;
  onSelect: (action: PropertiesComboboxAction) => void;
  exclude?: string[];
  include?: string[];
  categories?: PropertiesComboboxCategory[];
}

/**
 * Hard-coded session-level filters exposed alongside event/profile/group/cohort
 * dimensions. These map to the `session.*` filter prefix handled by the DB
 * filter-where utility.
 */
const SESSION_ACTIONS: PropertiesComboboxAction[] = [
  {
    value: 'session.is_bounce',
    label: 'Bounced',
    description: 'Single-pageview session',
  },
  {
    value: 'session.screen_view_count',
    label: 'Screen views',
    description: 'Number of screen views in the session',
  },
  {
    value: 'session.event_count',
    label: 'Events',
    description: 'Total events in the session',
  },
  {
    value: 'session.duration',
    label: 'Duration',
    description: 'Session length in milliseconds',
  },
  {
    value: 'session.revenue',
    label: 'Revenue',
    description: 'Revenue attributed to the session',
  },
  {
    value: 'session.has_replay',
    label: 'Has replay',
    description: 'Session has recorded replay chunks',
  },
  {
    value: 'session.performed_event',
    label: 'Performed event',
    description: 'Session contains an event named X',
  },
];

const DEFAULT_CATEGORIES: PropertiesComboboxCategory[] = [
  'event',
  'profile',
  'group',
];

function SearchHeader({
  onBack,
  onSearch,
  value,
}: {
  onBack?: () => void;
  onSearch: (value: string) => void;
  value: string;
}) {
  return (
    <div className="row items-center gap-1">
      {!!onBack && (
        <Button
          aria-label="Back to property categories"
          onClick={onBack}
          size="icon"
          variant="ghost"
        >
          <ArrowLeftIcon className="size-4" />
        </Button>
      )}
      <Input
        autoFocus
        onChange={(e) => onSearch(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === 'ArrowDown') {
            e.preventDefault();
            e.stopPropagation();
            e.currentTarget
              .closest('[role="menu"]')
              ?.querySelector<HTMLElement>('[role="menuitem"]')
              ?.focus();
          } else if (e.key.length === 1) {
            // Typing belongs to search, not the menu's typeahead navigation.
            e.stopPropagation();
          }
        }}
        placeholder="Search"
        value={value}
      />
    </div>
  );
}

export function PropertiesCombobox({
  event,
  events,
  customEventIds,
  children,
  onSelect,
  categories = DEFAULT_CATEGORIES,
  exclude = [],
  include = [],
}: PropertiesComboboxProps) {
  const { projectId } = useAppParams();
  const trpc = useTRPC();
  const [open, setOpen] = useState(false);
  const propertyQuery = useEventPropertyOptions(
    projectId,
    event
      ? event.customEventId
        ? []
        : (event.eventNames ?? [event.name])
      : events,
    event?.customEventId ? [event.customEventId] : customEventIds
  );
  const { properties } = propertyQuery;
  const profileProperties = useProfileProperties(projectId, {
    enabled: categories.includes('profile'),
  });
  const groupPropertiesQuery = useQuery(
    trpc.group.properties.queryOptions(
      { projectId },
      { enabled: categories.includes('group') }
    )
  );

  /**
   * Skip the index screen when only one category is reachable — except for
   * `cohort`, which has no sub-list (clicking it emits a generic cohort
   * filter action directly).
   */
  const initialState: State =
    categories.length === 1 && categories[0] !== 'cohort'
      ? (categories[0] as Exclude<PropertiesComboboxCategory, 'cohort'>)
      : 'index';

  const [state, setState] = useState<State>(initialState);
  const [search, setSearch] = useState('');
  const [visibleRows, setVisibleRows] = useState(100);
  useEffect(() => {
    setVisibleRows(100);
  }, [search, state, open]);
  const [direction, setDirection] = useState<'forward' | 'backward'>('forward');

  useEffect(() => {
    if (!open) {
      setState(initialState);
      setSearch('');
    }
  }, [open, initialState]);

  const matchesPropertyPattern = (property: string, pattern: string) => {
    if (pattern.endsWith('*')) {
      return property.startsWith(pattern.slice(0, -1));
    }
    return property === pattern;
  };

  const shouldShowProperty = (property: string) => {
    return !exclude.some((pattern) =>
      matchesPropertyPattern(property, pattern)
    );
  };

  const allProperties = Array.from(new Set([...properties, ...include]));

  const groupActions = [
    { value: 'group.name', label: 'name', description: 'group' },
    { value: 'group.type', label: 'type', description: 'group' },
    ...(groupPropertiesQuery.data ?? []).map((key) => ({
      value: `group.properties.${key}`,
      label: key,
      description: 'group.properties',
    })),
  ].filter((a) => shouldShowProperty(a.value));

  const profileActions = getProfilePropertyNames({
    eventProperties: properties,
    profileProperties,
    includedProperties: include,
  })
    .filter(shouldShowProperty)
    .map((property) => ({
      value: property,
      label: property.split('.').pop() ?? property,
      description: property.split('.').slice(0, -1).join('.'),
    }));
  const eventActions = allProperties
    .filter(
      (property) =>
        !property.startsWith('profile.') && shouldShowProperty(property)
    )
    .map((property) => ({
      value: property,
      label: property.split('.').pop() ?? property,
      description: property.split('.').slice(0, -1).join('.'),
      section: getEventPropertySection(property, profileProperties),
    }))
    .sort(
      (a, b) =>
        EVENT_PROPERTY_SECTIONS.indexOf(a.section) -
          EVENT_PROPERTY_SECTIONS.indexOf(b.section) ||
        a.value.localeCompare(b.value)
    );
  const sessionActions = SESSION_ACTIONS.filter((a) =>
    shouldShowProperty(a.value)
  );

  const handleStateChange = (newState: State) => {
    setDirection(newState === 'index' ? 'backward' : 'forward');
    setState(newState);
  };

  const handleSelect = (action: PropertiesComboboxAction) => {
    setOpen(false);
    onSelect(action);
  };

  const showBackButton = categories.length > 1;

  const renderIndex = () => {
    return (
      <DropdownMenuGroup>
        {categories.includes('event') && (
          <DropdownMenuItem
            className="group justify-between gap-2"
            onClick={(e) => {
              e.preventDefault();
              handleStateChange('event');
            }}
          >
            Event properties
            <DatabaseIcon className="size-4 transition-all group-hover:rotate-12 group-hover:scale-125 group-hover:text-blue-500" />
          </DropdownMenuItem>
        )}
        {categories.includes('profile') && (
          <DropdownMenuItem
            className="group justify-between gap-2"
            onClick={(e) => {
              e.preventDefault();
              handleStateChange('profile');
            }}
          >
            Profile properties
            <UserIcon className="size-4 transition-all group-hover:rotate-12 group-hover:scale-125 group-hover:text-blue-500" />
          </DropdownMenuItem>
        )}
        {categories.includes('group') && (
          <DropdownMenuItem
            className="group justify-between gap-2"
            onClick={(e) => {
              e.preventDefault();
              handleStateChange('group');
            }}
          >
            Group properties
            <Building2Icon className="size-4 transition-all group-hover:rotate-12 group-hover:scale-125 group-hover:text-blue-500" />
          </DropdownMenuItem>
        )}
        {categories.includes('cohort') && (
          <DropdownMenuItem
            className="group justify-between gap-2"
            onClick={(e) => {
              e.preventDefault();
              handleSelect({
                value: 'cohort',
                label: 'Cohorts',
                description: 'All cohorts',
              });
            }}
          >
            Cohorts
            <TargetIcon className="size-4 transition-all group-hover:rotate-12 group-hover:scale-125 group-hover:text-blue-500" />
          </DropdownMenuItem>
        )}
        {categories.includes('session') && (
          <DropdownMenuItem
            className="group justify-between gap-2"
            onClick={(e) => {
              e.preventDefault();
              handleStateChange('session');
            }}
          >
            Session metrics
            <ActivityIcon className="size-4 transition-all group-hover:rotate-12 group-hover:scale-125 group-hover:text-blue-500" />
          </DropdownMenuItem>
        )}
      </DropdownMenuGroup>
    );
  };

  const renderActionList = (
    actions: PropertiesComboboxAction[],
    options: { eventProperties?: boolean } = {}
  ) => {
    const filtered = actions.filter(
      (action) =>
        action.label.toLowerCase().includes(search.toLowerCase()) ||
        action.description.toLowerCase().includes(search.toLowerCase())
    );

    type Row = { value: string; heading: string } | PropertiesComboboxAction;
    const rows: Row[] = [];
    let section: string | undefined;
    for (const action of filtered) {
      if (action.section && action.section !== section) {
        section = action.section;
        rows.push({ value: `heading:${section}`, heading: section });
      }
      rows.push(action);
    }

    return (
      <div className="col">
        <SearchHeader
          onBack={showBackButton ? () => handleStateChange('index') : undefined}
          onSearch={setSearch}
          value={search}
        />
        <DropdownMenuSeparator />
        {options.eventProperties && propertyQuery.isError && (
          <div className="p-2 text-sm" role="alert">
            Some properties could not be loaded.
            <Button variant="ghost" size="sm" onClick={propertyQuery.retry}>
              Retry
            </Button>
          </div>
        )}
        {options.eventProperties &&
          propertyQuery.isPending &&
          filtered.length > 0 && (
            <div className="p-2 text-xs text-muted-foreground" role="status">
              Loading more properties…
            </div>
          )}
        {filtered.length === 0 ? (
          <div
            className="p-3 text-center text-sm text-muted-foreground"
            role="status"
          >
            {options.eventProperties && propertyQuery.isPending
              ? 'Loading properties…'
              : options.eventProperties && propertyQuery.isError
                ? 'Retry to load event properties.'
                : 'No properties found'}
          </div>
        ) : (
          <div className="max-h-[300px] overflow-y-auto">
            {rows.slice(0, visibleRows).map((action, index) =>
              'heading' in action ? (
                <div
                  key={action.value}
                  className="px-2 pb-1 pt-3 text-xs font-semibold text-muted-foreground"
                >
                  {action.heading}
                </div>
              ) : (
                <DropdownMenuItem
                  key={action.value}
                  data-property-row={index}
                  className="col items-start cursor-pointer gap-px rounded-md p-2"
                  onSelect={() => handleSelect(action)}
                >
                  <div className="font-medium [overflow-wrap:anywhere]">
                    {action.label}
                  </div>
                  {action.description &&
                    action.description !== 'properties' && (
                      <div className="text-muted-foreground text-sm [overflow-wrap:anywhere]">
                        {action.description}
                      </div>
                    )}
                </DropdownMenuItem>
              )
            )}
            {rows.length > visibleRows && (
              <DropdownMenuItem
                onSelect={(event) => {
                  event.preventDefault();
                  const menu = event.target as HTMLElement;
                  const container = menu.parentElement;
                  setVisibleRows((count) => count + 100);
                  requestAnimationFrame(() => {
                    const next = Array.from(
                      container?.querySelectorAll<HTMLElement>(
                        '[data-property-row]'
                      ) ?? []
                    ).find(
                      (item) => Number(item.dataset.propertyRow) >= visibleRows
                    );
                    next?.focus();
                  });
                }}
              >
                Show more properties ({rows.length - visibleRows})
              </DropdownMenuItem>
            )}
          </div>
        )}
      </div>
    );
  };

  return (
    <DropdownMenu
      onOpenChange={(open) => {
        setOpen(open);
      }}
      open={open}
    >
      <DropdownMenuTrigger asChild>{children(setOpen)}</DropdownMenuTrigger>
      <DropdownMenuContent align="start" className="max-w-80">
        <AnimatePresence initial={false} mode="wait">
          {state === 'index' && (
            <motion.div
              animate={{ opacity: 1 }}
              exit={{ opacity: 0 }}
              initial={{ opacity: 0 }}
              key="index"
              transition={{ duration: 0.05 }}
            >
              {renderIndex()}
            </motion.div>
          )}
          {state === 'event' && (
            <motion.div
              animate={{ opacity: 1, x: 0 }}
              exit={{ opacity: 0, x: direction === 'forward' ? -20 : 20 }}
              initial={{ opacity: 0, x: direction === 'forward' ? 20 : -20 }}
              key="event"
              transition={{ duration: 0.05 }}
            >
              {renderActionList(eventActions, { eventProperties: true })}
            </motion.div>
          )}
          {state === 'profile' && (
            <motion.div
              animate={{ opacity: 1, x: 0 }}
              exit={{ opacity: 0, x: direction === 'forward' ? -20 : 20 }}
              initial={{ opacity: 0, x: direction === 'forward' ? 20 : -20 }}
              key="profile"
              transition={{ duration: 0.05 }}
            >
              {renderActionList(profileActions)}
            </motion.div>
          )}
          {state === 'group' && (
            <motion.div
              animate={{ opacity: 1, x: 0 }}
              exit={{ opacity: 0, x: direction === 'forward' ? -20 : 20 }}
              initial={{ opacity: 0, x: direction === 'forward' ? 20 : -20 }}
              key="group"
              transition={{ duration: 0.05 }}
            >
              {renderActionList(groupActions)}
            </motion.div>
          )}
          {state === 'session' && (
            <motion.div
              animate={{ opacity: 1, x: 0 }}
              exit={{ opacity: 0, x: direction === 'forward' ? -20 : 20 }}
              initial={{ opacity: 0, x: direction === 'forward' ? 20 : -20 }}
              key="session"
              transition={{ duration: 0.05 }}
            >
              {renderActionList(sessionActions)}
            </motion.div>
          )}
        </AnimatePresence>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
