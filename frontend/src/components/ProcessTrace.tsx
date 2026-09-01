import { Check, CircleAlert, LoaderCircle, Search, Wrench } from "lucide-react";

import type { AgentEvent } from "../types";

function eventIcon(event: AgentEvent) {
  if (event.status === "error" || event.status === "warning") return <CircleAlert size={14} />;
  if (event.status === "running") return <LoaderCircle className="spin" size={14} />;
  if (event.phase === "action") return <Wrench size={14} />;
  if (event.phase === "observe") return <Search size={14} />;
  return <Check size={14} />;
}

export function ProcessTrace({ events }: { events: AgentEvent[] }) {
  if (!events.length) return null;
  return (
    <div className="process-trace" aria-label="智能体执行过程">
      <div className="process-title">模型思考中</div>
      {events.map((event, index) => (
        <div className={`process-item ${event.status}`} key={`${event.phase}-${index}`}>
          <span className="process-icon">{eventIcon(event)}</span>
          <span>
            <strong>{event.title}</strong>
            {event.detail && <small>{event.detail}</small>}
          </span>
        </div>
      ))}
    </div>
  );
}
