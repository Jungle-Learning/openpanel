import type { IChartEventItem } from '@openpanel/validation';
import { PiIcon, PlusIcon } from 'lucide-react';
import { buildFormulaFromTemplate, formulaTemplates } from './formula-helpers';
import { Button } from '@/components/ui/button';
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu';

interface FormulaTemplateMenuProps {
  series: IChartEventItem[];
  formulaIndex: number;
  onSelect: (formula: string) => void;
  compact?: boolean;
}

export function FormulaTemplateMenu({
  series,
  formulaIndex,
  onSelect,
  compact = false,
}: FormulaTemplateMenuProps) {
  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button
          className={
            compact
              ? 'justify-start text-left'
              : 'flex-1 justify-start px-4 text-left'
          }
          icon={PiIcon}
          type="button"
          variant="outline"
        >
          {compact ? 'Choose calculation' : 'Add formula'}
          {!compact && (
            <PlusIcon className="ml-auto size-4 text-muted-foreground" />
          )}
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="start" className="w-96 max-w-[90vw]">
        {formulaTemplates.map((template, index) => {
          const formula = buildFormulaFromTemplate({
            templateId: template.id,
            series,
            formulaIndex,
          });
          const requiresMoreSeries = formula === null;

          return (
            <div key={template.id}>
              {index === formulaTemplates.length - 1 && (
                <DropdownMenuSeparator />
              )}
              <DropdownMenuItem
                className="items-start gap-3 py-2"
                disabled={requiresMoreSeries}
                onSelect={() => {
                  if (formula !== null) {
                    onSelect(formula);
                  }
                }}
              >
                <div className="min-w-0 flex-1">
                  <div className="flex items-baseline justify-between gap-3">
                    <span className="font-medium">{template.label}</span>
                    <code className="shrink-0 text-muted-foreground text-xs">
                      {template.example}
                    </code>
                  </div>
                  <div className="mt-0.5 text-muted-foreground text-xs leading-relaxed">
                    {requiresMoreSeries
                      ? 'Add at least two eligible earlier series to use this calculation.'
                      : template.description}
                  </div>
                </div>
              </DropdownMenuItem>
            </div>
          );
        })}
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
