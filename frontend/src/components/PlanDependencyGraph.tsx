import {
  AtSign,
  CalendarDays,
  Check,
  FileText,
  Globe2,
  ListChecks,
  LockKeyhole,
  Mail,
} from "lucide-react";
import type { CSSProperties, KeyboardEvent } from "react";

import type { PlanAction, RunStatus } from "@/lib/types";

import styles from "./workspace.module.css";

const NODE_WIDTH = 188;
const NODE_HEIGHT = 60;
const COLUMN_GAP = 48;
const ROW_GAP = 20;
const CANVAS_PADDING = 24;
const SERVICE_LABELS: Record<string, string> = {
  web: "Web",
  mail: "Mail",
  calendar: "Calendar",
  tasks: "Tasks",
  files: "Files",
  x: "X",
};

interface PlanDependencyGraphProps {
  actions: PlanAction[];
  runStatus: RunStatus;
  selectedActionId: string | null;
  onSelectAction: (actionId: string) => void;
}

interface PositionedAction {
  action: PlanAction;
  x: number;
  y: number;
}

export function PlanDependencyGraph({
  actions,
  runStatus,
  selectedActionId,
  onSelectAction,
}: PlanDependencyGraphProps) {
  const validationError = validateGraph(actions);
  if (validationError) {
    return (
      <div className={styles.graphFallback} role="status">
        Dependency view unavailable. Use the action sequence for this plan.
      </div>
    );
  }

  const layout = layoutActions(actions);
  const byId = new Map(layout.nodes.map((node) => [node.action.id, node]));
  const edges = actions.flatMap((action) => (
    action.depends_on.map((dependencyId) => ({
      from: byId.get(dependencyId)!,
      to: byId.get(action.id)!,
    }))
  ));

  return (
    <div className={styles.graphSurface}>
      <div
        className={styles.graphDesktopViewport}
        role="group"
        aria-label="Plan dependency graph"
      >
        <div
          className={styles.graphCanvas}
          style={{ width: layout.width, height: layout.height }}
        >
          <svg
            className={styles.graphEdges}
            width={layout.width}
            height={layout.height}
            viewBox={`0 0 ${layout.width} ${layout.height}`}
            aria-hidden="true"
          >
            <defs>
              <marker
                id="daypilot-dependency-arrow"
                viewBox="0 0 6 6"
                refX="5"
                refY="3"
                markerWidth="5"
                markerHeight="5"
                orient="auto"
              >
                <path d="M0 0L6 3L0 6Z" />
              </marker>
            </defs>
            {edges.map(({ from, to }) => (
              <path
                key={`${from.action.id}-${to.action.id}`}
                data-testid="dependency-edge"
                d={edgePath(from, to, layout.direction)}
                markerEnd="url(#daypilot-dependency-arrow)"
              />
            ))}
          </svg>
          {layout.nodes.map(({ action, x, y }) => (
            <GraphNode
              key={action.id}
              action={action}
              selected={selectedActionId === action.id}
              runStatus={runStatus}
              accessibleLabel={accessibleDependency(action, actions)}
              style={{ left: x, top: y, width: NODE_WIDTH, height: NODE_HEIGHT }}
              onSelect={() => onSelectAction(action.id)}
            />
          ))}
        </div>
      </div>

      <div className={styles.graphMobile} role="group" aria-label="Plan dependencies">
        {actions.map((action) => (
          <GraphNode
            key={action.id}
            action={action}
            selected={selectedActionId === action.id}
            runStatus={runStatus}
            accessibleLabel={accessibleDependency(action, actions)}
            onSelect={() => onSelectAction(action.id)}
            mobile
            dependencyLabel={dependencyLabel(action, actions)}
          />
        ))}
      </div>

      <ul className={styles.srOnly}>
        {actions.map((action) => (
          <li key={action.id}>{accessibleDependency(action, actions)}</li>
        ))}
      </ul>
    </div>
  );
}

function GraphNode({
  action,
  selected,
  runStatus,
  style,
  onSelect,
  mobile = false,
  dependencyLabel: dependencyText,
  accessibleLabel,
}: {
  action: PlanAction;
  selected: boolean;
  runStatus: RunStatus;
  style?: CSSProperties;
  onSelect: () => void;
  mobile?: boolean;
  dependencyLabel?: string;
  accessibleLabel: string;
}) {
  const completed = ["completed", "executed", "verified"].includes(action.status);
  const failed = action.status === "failed";
  const waitingWrite = runStatus === "waiting_approval" && action.side_effecting;

  function onKeyDown(event: KeyboardEvent<HTMLButtonElement>) {
    if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      onSelect();
    }
  }

  return (
    <button
      type="button"
      className={`${styles.graphNode} ${action.side_effecting ? styles.graphNodeWrite : styles.graphNodeRead} ${selected ? styles.graphNodeSelected : ""} ${completed ? styles.graphNodeCompleted : ""} ${failed ? styles.graphNodeFailed : ""} ${waitingWrite ? styles.graphNodeApproval : ""} ${mobile ? styles.graphNodeMobile : ""}`}
      style={style}
      data-action-id={action.id}
      data-testid={`${mobile ? "graph-mobile-node" : "graph-node"}-${action.id}`}
      title={action.description}
      aria-label={accessibleLabel}
      onClick={onSelect}
      onKeyDown={onKeyDown}
    >
      <span className={styles.graphNodeIcon}>{iconFor(action.server_name)}</span>
      <span className={styles.graphNodeCopy}>
        <strong>{graphTitle(action)}</strong>
        <span><code>{action.tool_name}</code> · {serviceLabel(action.server_name)}</span>
        {dependencyText && <small>{dependencyText}</small>}
      </span>
      <span className={styles.graphNodeMode}>
        {completed ? <Check size={10} /> : waitingWrite ? <LockKeyhole size={10} /> : null}
        {action.side_effecting ? "Write" : "Read"}
      </span>
    </button>
  );
}

function layoutActions(actions: PlanAction[]) {
  const depths = dependencyDepths(actions);
  const maxDepth = Math.max(0, ...depths.values());
  const allIndependent = actions.every((action) => action.depends_on.length === 0);
  const positioned: PositionedAction[] = [];

  if (allIndependent) {
    actions.forEach((action, index) => {
      positioned.push({
        action,
        x: CANVAS_PADDING + index * (NODE_WIDTH + COLUMN_GAP),
        y: CANVAS_PADDING,
      });
    });
    return {
      nodes: positioned,
      width: CANVAS_PADDING * 2 + actions.length * NODE_WIDTH
        + Math.max(0, actions.length - 1) * COLUMN_GAP,
      height: 180,
      direction: "horizontal" as const,
    };
  }

  const layers = Array.from({ length: maxDepth + 1 }, (_, depth) => (
    actions.filter((action) => depths.get(action.id) === depth)
  ));
  const tallestLayer = Math.max(...layers.map((layer) => layer.length));

  if (maxDepth >= 3) {
    const verticalPadding = 12;
    const widestLayer = Math.max(...layers.map((layer) => layer.length));
    const width = Math.max(
      560,
      widestLayer * NODE_WIDTH + Math.max(0, widestLayer - 1) * ROW_GAP
        + CANVAS_PADDING * 2,
    );
    const depthGap = 12;
    layers.forEach((layer, depth) => {
      const layerWidth = layer.length * NODE_WIDTH + Math.max(0, layer.length - 1) * ROW_GAP;
      const startX = (width - layerWidth) / 2;
      layer.forEach((action, column) => {
        positioned.push({
          action,
          x: startX + column * (NODE_WIDTH + ROW_GAP),
          y: verticalPadding + depth * (NODE_HEIGHT + depthGap),
        });
      });
    });
    return {
      nodes: positioned,
      width,
      height: verticalPadding * 2 + (maxDepth + 1) * NODE_HEIGHT + maxDepth * depthGap,
      direction: "vertical" as const,
    };
  }

  const contentHeight = tallestLayer * NODE_HEIGHT + Math.max(0, tallestLayer - 1) * ROW_GAP;
  const height = Math.max(190, contentHeight + CANVAS_PADDING * 2);

  layers.forEach((layer, depth) => {
    const layerHeight = layer.length * NODE_HEIGHT + Math.max(0, layer.length - 1) * ROW_GAP;
    const startY = (height - layerHeight) / 2;
    layer.forEach((action, row) => {
      positioned.push({
        action,
        x: CANVAS_PADDING + depth * (NODE_WIDTH + COLUMN_GAP),
        y: startY + row * (NODE_HEIGHT + ROW_GAP),
      });
    });
  });

  return {
    nodes: positioned,
    width: CANVAS_PADDING * 2 + (maxDepth + 1) * NODE_WIDTH + maxDepth * COLUMN_GAP,
    height,
    direction: "horizontal" as const,
  };
}

function dependencyDepths(actions: PlanAction[]) {
  const byId = new Map(actions.map((action) => [action.id, action]));
  const cache = new Map<string, number>();
  const depth = (action: PlanAction): number => {
    const cached = cache.get(action.id);
    if (cached !== undefined) return cached;
    const value = action.depends_on.length === 0
      ? 0
      : 1 + Math.max(...action.depends_on.map((id) => depth(byId.get(id)!)));
    cache.set(action.id, value);
    return value;
  };
  actions.forEach((action) => depth(action));
  return cache;
}

function edgePath(
  from: PositionedAction,
  to: PositionedAction,
  direction: "horizontal" | "vertical",
) {
  if (direction === "vertical") {
    const startX = from.x + NODE_WIDTH / 2;
    const startY = from.y + NODE_HEIGHT;
    const endX = to.x + NODE_WIDTH / 2;
    const endY = to.y;
    const curve = Math.max(18, (endY - startY) * 0.48);
    return `M${startX} ${startY} C${startX} ${startY + curve}, ${endX} ${endY - curve}, ${endX} ${endY}`;
  }
  const startX = from.x + NODE_WIDTH;
  const startY = from.y + NODE_HEIGHT / 2;
  const endX = to.x;
  const endY = to.y + NODE_HEIGHT / 2;
  const curve = Math.max(24, (endX - startX) * 0.48);
  return `M${startX} ${startY} C${startX + curve} ${startY}, ${endX - curve} ${endY}, ${endX} ${endY}`;
}

function validateGraph(actions: PlanAction[]) {
  const ids = new Set(actions.map((action) => action.id));
  const positions = new Map(actions.map((action, index) => [action.id, index]));
  if (ids.size !== actions.length) return "duplicate";
  for (const action of actions) {
    if (new Set(action.depends_on).size !== action.depends_on.length) return "duplicate";
    if (action.depends_on.includes(action.id)) return "self";
    if (action.depends_on.some((dependency) => !ids.has(dependency))) return "missing";
    if (action.depends_on.some((dependency) => positions.get(dependency)! >= positions.get(action.id)!)) {
      return "ordering";
    }
  }
  const visiting = new Set<string>();
  const visited = new Set<string>();
  const byId = new Map(actions.map((action) => [action.id, action]));
  const visit = (id: string): boolean => {
    if (visiting.has(id)) return false;
    if (visited.has(id)) return true;
    visiting.add(id);
    for (const dependency of byId.get(id)!.depends_on) {
      if (!visit(dependency)) return false;
    }
    visiting.delete(id);
    visited.add(id);
    return true;
  };
  return actions.every((action) => visit(action.id)) ? null : "cycle";
}

function graphTitle(action: PlanAction) {
  const titles: Record<string, string> = {
    search_web: "Research public sources",
    search_mail: "Find matching mail",
    get_thread: "Read grounded thread",
    get_message: "Read message",
    list_events: "Check calendar",
    find_free_slots: "Find free time",
    create_event: "Create calendar event",
    list_tasks: "Review current tasks",
    create_task: "Create task",
    create_task_batch: "Create preparation tasks",
    complete_task: "Complete task",
    search_files: "Find workspace files",
    read_file: "Read workspace file",
    create_draft: "Create email draft",
    search_posts: "Search X posts",
    create_post_draft: "Create X draft",
    publish_post: "Publish X post",
  };
  return titles[action.tool_name] ?? action.description;
}

function serviceLabel(serverName: string) {
  return SERVICE_LABELS[serverName] ?? serverName;
}

function iconFor(serverName: string) {
  if (serverName === "web") return <Globe2 size={15} />;
  if (serverName === "mail") return <Mail size={15} />;
  if (serverName === "calendar") return <CalendarDays size={15} />;
  if (serverName === "tasks") return <ListChecks size={15} />;
  if (serverName === "x") return <AtSign size={15} />;
  return <FileText size={15} />;
}

function dependencyLabel(action: PlanAction, actions: PlanAction[]) {
  if (action.depends_on.length === 0) return "Starts independently";
  const byId = new Map(actions.map((candidate) => [candidate.id, candidate]));
  return `Depends on ${action.depends_on.map((id) => graphTitle(byId.get(id)!)).join(", ")}`;
}

function accessibleDependency(action: PlanAction, actions: PlanAction[]) {
  const label = `${graphTitle(action)}. ${action.side_effecting ? "Write" : "Read"}.`;
  if (actions.length === 1 || action.depends_on.length === 0) return `${label} Starts independently.`;
  return `${label} ${dependencyLabel(action, actions)}.`;
}
