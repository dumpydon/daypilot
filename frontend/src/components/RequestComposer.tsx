import { ArrowRight, ChevronDown, Sparkles } from "lucide-react";
import { type FormEvent, type KeyboardEvent as ReactKeyboardEvent, useEffect, useRef, useState } from "react";

import { HeroFlowCanvas } from "./HeroFlowCanvas";
import { RotatingHeroWord } from "./RotatingHeroWord";
import styles from "./workspace.module.css";

const examples = [
  { label: "Interview preparation", prompt: "Prepare me for my interview with Rahul tomorrow." },
  { label: "Follow up", prompt: "Find my last conversation with Rahul and create a follow-up draft." },
  { label: "Focus block", prompt: "Find a free 90-minute focus block tonight and schedule it." },
  { label: "Preparation checklist", prompt: "Create a preparation checklist for my interview tomorrow." },
];

interface RequestComposerProps {
  onSubmit: (goal: string) => Promise<void> | void;
  busy: boolean;
}

export function RequestComposer({ onSubmit, busy }: RequestComposerProps) {
  const [goal, setGoal] = useState("");
  const [pickerOpen, setPickerOpen] = useState(false);
  const [highlightedIndex, setHighlightedIndex] = useState(0);
  const [composerHovered, setComposerHovered] = useState(false);
  const [composerFocused, setComposerFocused] = useState(false);
  const pickerRef = useRef<HTMLDivElement>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const submissionInFlightRef = useRef(false);
  const interactive = composerHovered || composerFocused;

  useEffect(() => {
    if (!pickerOpen) return;
    const closeOnOutsidePointer = (event: PointerEvent) => {
      if (pickerRef.current && !pickerRef.current.contains(event.target as Node)) setPickerOpen(false);
    };
    const handleKeyboard = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        setPickerOpen(false);
      } else if (event.key === "ArrowDown") {
        event.preventDefault();
        setHighlightedIndex((index) => (index + 1) % examples.length);
      } else if (event.key === "ArrowUp") {
        event.preventDefault();
        setHighlightedIndex((index) => (index - 1 + examples.length) % examples.length);
      } else if (event.key === "Home") {
        event.preventDefault();
        setHighlightedIndex(0);
      } else if (event.key === "End") {
        event.preventDefault();
        setHighlightedIndex(examples.length - 1);
      } else if (event.key === "Enter") {
        event.preventDefault();
        chooseDemo(examples[highlightedIndex].prompt);
      }
    };
    window.addEventListener("pointerdown", closeOnOutsidePointer);
    window.addEventListener("keydown", handleKeyboard);
    return () => {
      window.removeEventListener("pointerdown", closeOnOutsidePointer);
      window.removeEventListener("keydown", handleKeyboard);
    };
  }, [highlightedIndex, pickerOpen]);

  async function submit(event: FormEvent) {
    event.preventDefault();
    await submitGoal();
  }

  async function submitGoal() {
    const nextGoal = goal.trim();
    if (!nextGoal || busy || submissionInFlightRef.current) return;
    submissionInFlightRef.current = true;
    try {
      await onSubmit(nextGoal);
    } finally {
      submissionInFlightRef.current = false;
    }
  }

  function handleGoalKeyDown(event: ReactKeyboardEvent<HTMLTextAreaElement>) {
    if (event.key !== "Enter" || event.shiftKey || event.nativeEvent.isComposing) return;
    event.preventDefault();
    void submitGoal();
  }

  function chooseDemo(prompt: string) {
    setGoal(prompt);
    setPickerOpen(false);
    requestAnimationFrame(() => textareaRef.current?.focus());
  }

  return (
    <section className={styles.composerFrame}>
      <HeroFlowCanvas interactive={interactive} />
      <div className={styles.composer}>
        <div className={styles.composerHeading}>
          <h1 className={styles.heroTitle}>
            <span className={styles.heroLine}>An MCP agent that <RotatingHeroWord interactive={interactive} /></span>
            <span className={styles.heroLine}>across your workspace</span>
          </h1>
          <p className={styles.heroSupport}>Reads happen automatically. Every external change pauses for your approval.</p>
        </div>
        <form
          onSubmit={submit}
          className={`${styles.requestForm} ${goal.trim() ? styles.requestFormReady : ""}`}
          data-testid="request-composer"
          onPointerEnter={() => setComposerHovered(true)}
          onPointerLeave={() => setComposerHovered(false)}
          onFocusCapture={() => setComposerFocused(true)}
          onBlurCapture={(event) => {
            if (!event.currentTarget.contains(event.relatedTarget as Node | null)) setComposerFocused(false);
          }}
        >
          <label htmlFor="goal">Goal</label>
          <textarea
            id="goal"
            ref={textareaRef}
            value={goal}
            onChange={(event) => setGoal(event.target.value)}
            onKeyDown={handleGoalKeyDown}
            placeholder="Prepare me for my interview with Simon tomorrow."
            rows={2}
            disabled={busy}
          />
          <button type="submit" disabled={busy || !goal.trim()} aria-label="Start DayPilot run" title="Start DayPilot run">
            <ArrowRight size={19} strokeWidth={2.3} />
          </button>
        </form>
        <div className={styles.demoControl} ref={pickerRef}>
          <button
            type="button"
            className={styles.demoTrigger}
            aria-expanded={pickerOpen}
            aria-haspopup="menu"
            onClick={() => { setPickerOpen((value) => !value); setHighlightedIndex(0); }}
          >
            <Sparkles size={13} /> Try a demo <ChevronDown size={13} className={pickerOpen ? styles.chevronOpen : ""} />
          </button>
          {pickerOpen && (
            <div className={styles.demoPicker} role="menu" aria-label="Demo prompts">
              <div className={styles.demoPickerHeader}>Choose a starting point</div>
              {examples.map((example, index) => (
                <button
                  type="button"
                  role="menuitemradio"
                  aria-checked={highlightedIndex === index}
                  className={`${styles.demoOption} ${highlightedIndex === index ? styles.demoOptionActive : ""}`}
                  key={example.label}
                  onMouseEnter={() => setHighlightedIndex(index)}
                  onClick={() => chooseDemo(example.prompt)}
                >
                  <strong>{example.label}</strong>
                  <span>{example.prompt}</span>
                </button>
              ))}
            </div>
          )}
        </div>
      </div>
    </section>
  );
}
