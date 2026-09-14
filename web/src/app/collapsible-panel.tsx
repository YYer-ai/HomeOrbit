import type { ReactNode } from "react";

export function CollapsiblePanel({ className, label, children }: {
  className: string;
  label: string;
  children: ReactNode;
}) {
  return (
    <aside className={className} aria-label={label}>
      <details className="panel-disclosure" open>
        <summary className="panel-toggle">
          <span>{label}</span>
          <span className="panel-action-collapse">收起</span>
          <span className="panel-action-expand">展开</span>
        </summary>
        <div className="panel-content">{children}</div>
      </details>
    </aside>
  );
}
