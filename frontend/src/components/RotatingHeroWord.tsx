"use client";

import { type CSSProperties, useEffect, useRef, useState } from "react";

import styles from "./workspace.module.css";
import { DAYPILOT_BLUE } from "./visualTokens";

export const HERO_WORDS = ["organizes", "schedules", "coordinates"] as const;

export const HERO_MOTION = {
  idleDwellMs: 2_000,
  interactiveDwellMs: 600,
  transitionMs: 400,
  easing: "ease",
  translateYpx: 20,
  blurPx: 3,
  accent: DAYPILOT_BLUE,
} as const;

export type HeroWordPhase = "settled" | "exiting" | "entering";

export interface HeroWordSnapshot {
  index: number;
  phase: HeroWordPhase;
}

const INITIAL_SNAPSHOT: HeroWordSnapshot = { index: 0, phase: "settled" };

type TimerHandle = ReturnType<typeof setTimeout>;
type SetTimer = (callback: () => void, delay: number) => TimerHandle;
type ClearTimer = (timer: TimerHandle) => void;

interface SchedulerOptions {
  onChange: (snapshot: HeroWordSnapshot) => void;
  setTimer?: SetTimer;
  clearTimer?: ClearTimer;
  reducedMotion?: boolean;
}

/** One timer owns the complete dwell → exit → enter cycle. */
export class RotatingWordScheduler {
  private snapshot = INITIAL_SNAPSHOT;
  private timer: TimerHandle | null = null;
  private running = false;
  private interactive = false;
  private reducedMotion: boolean;
  private readonly onChange: SchedulerOptions["onChange"];
  private readonly setTimer: SetTimer;
  private readonly clearTimer: ClearTimer;

  constructor({
    onChange,
    setTimer = (callback, delay) => setTimeout(callback, delay),
    clearTimer = (timer) => clearTimeout(timer),
    reducedMotion = false,
  }: SchedulerOptions) {
    this.onChange = onChange;
    this.setTimer = setTimer;
    this.clearTimer = clearTimer;
    this.reducedMotion = reducedMotion;
  }

  start() {
    if (this.running) return;
    this.running = true;
    if (!this.reducedMotion) this.scheduleDwell();
  }

  stop() {
    this.running = false;
    this.clearScheduledTimer();
  }

  setInteractive(interactive: boolean) {
    if (this.interactive === interactive) return;
    this.interactive = interactive;
    if (this.running && !this.reducedMotion && this.snapshot.phase === "settled") {
      this.scheduleDwell();
    }
  }

  setReducedMotion(reducedMotion: boolean) {
    if (this.reducedMotion === reducedMotion) return;
    this.reducedMotion = reducedMotion;
    this.clearScheduledTimer();
    if (reducedMotion) {
      this.snapshot = INITIAL_SNAPSHOT;
      this.onChange(this.snapshot);
    } else if (this.running) {
      this.scheduleDwell();
    }
  }

  private scheduleDwell() {
    this.schedule(
      this.interactive ? HERO_MOTION.interactiveDwellMs : HERO_MOTION.idleDwellMs,
      () => this.beginExit(),
    );
  }

  private beginExit() {
    this.snapshot = { ...this.snapshot, phase: "exiting" };
    this.onChange(this.snapshot);
    this.schedule(HERO_MOTION.transitionMs, () => this.beginEntrance());
  }

  private beginEntrance() {
    this.snapshot = {
      index: (this.snapshot.index + 1) % HERO_WORDS.length,
      phase: "entering",
    };
    this.onChange(this.snapshot);
    this.schedule(HERO_MOTION.transitionMs, () => this.settle());
  }

  private settle() {
    this.snapshot = { ...this.snapshot, phase: "settled" };
    this.onChange(this.snapshot);
    this.scheduleDwell();
  }

  private schedule(delay: number, callback: () => void) {
    this.clearScheduledTimer();
    this.timer = this.setTimer(() => {
      this.timer = null;
      if (this.running && !this.reducedMotion) callback();
    }, delay);
  }

  private clearScheduledTimer() {
    if (this.timer === null) return;
    this.clearTimer(this.timer);
    this.timer = null;
  }
}

const motionVariables = {
  "--hero-transition-duration": `${HERO_MOTION.transitionMs}ms`,
  "--hero-transition-easing": HERO_MOTION.easing,
  "--hero-translate-y": `${HERO_MOTION.translateYpx}px`,
  "--hero-blur": `${HERO_MOTION.blurPx}px`,
  "--hero-accent": HERO_MOTION.accent,
} as CSSProperties;

export function RotatingHeroWord({ interactive }: { interactive: boolean }) {
  const [snapshot, setSnapshot] = useState<HeroWordSnapshot>(INITIAL_SNAPSHOT);
  const schedulerRef = useRef<RotatingWordScheduler | null>(null);

  useEffect(() => {
    const motionPreference = window.matchMedia?.("(prefers-reduced-motion: reduce)");
    const scheduler = new RotatingWordScheduler({
      onChange: setSnapshot,
      reducedMotion: motionPreference?.matches ?? false,
    });
    schedulerRef.current = scheduler;
    scheduler.start();

    const handleMotionPreference = () => scheduler.setReducedMotion(motionPreference?.matches ?? false);
    motionPreference?.addEventListener("change", handleMotionPreference);
    return () => {
      motionPreference?.removeEventListener("change", handleMotionPreference);
      scheduler.stop();
      schedulerRef.current = null;
    };
  }, []);

  useEffect(() => {
    schedulerRef.current?.setInteractive(interactive);
  }, [interactive]);

  return (
    <span className={styles.rotatingWord} style={motionVariables}>
      {HERO_WORDS.map((word) => (
        <span key={word} aria-hidden="true" className={styles.rotatingMeasure}>{word}</span>
      ))}
      <span
        className={styles.rotatingCurrent}
        data-phase={snapshot.phase}
        data-testid="rotating-action"
      >
        {HERO_WORDS[snapshot.index]}
      </span>
    </span>
  );
}
