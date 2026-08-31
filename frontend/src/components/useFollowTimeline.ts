import { useCallback, useEffect, useRef, useState } from "react";

const BOTTOM_THRESHOLD = 64;

interface FollowTimelineOptions {
  runId: string;
  itemKey: string;
  itemCount: number;
  isLive: boolean;
  startAtLatest: boolean;
}

export function useFollowTimeline({
  runId,
  itemKey,
  itemCount,
  isLive,
  startAtLatest,
}: FollowTimelineOptions) {
  const containerRef = useRef<HTMLDivElement>(null);
  const runIdRef = useRef("");
  const itemKeyRef = useRef("");
  const itemCountRef = useRef(0);
  const isLiveRef = useRef(false);
  const followingRef = useRef(isLive);
  const programmaticScrollRef = useRef(false);
  const releaseTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const [following, setFollowingState] = useState(isLive);
  const [newEvents, setNewEvents] = useState(0);

  const setFollowing = useCallback((value: boolean) => {
    followingRef.current = value;
    setFollowingState(value);
    if (value) setNewEvents(0);
  }, []);

  const releaseProgrammaticScroll = useCallback(() => {
    if (releaseTimerRef.current) clearTimeout(releaseTimerRef.current);
    releaseTimerRef.current = setTimeout(() => {
      programmaticScrollRef.current = false;
    }, 420);
  }, []);

  const scrollToLatest = useCallback((smooth: boolean) => {
    const container = containerRef.current;
    if (!container) return;
    const reducedMotion = typeof window !== "undefined"
      && window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;
    const behavior: ScrollBehavior = smooth && !reducedMotion ? "smooth" : "auto";
    programmaticScrollRef.current = true;
    if (typeof container.scrollTo === "function") {
      container.scrollTo({ top: container.scrollHeight, behavior });
    } else {
      container.scrollTop = container.scrollHeight;
    }
    releaseProgrammaticScroll();
  }, [releaseProgrammaticScroll]);

  useEffect(() => () => {
    if (releaseTimerRef.current) clearTimeout(releaseTimerRef.current);
  }, []);

  useEffect(() => {
    const switchedRun = runIdRef.current !== runId;
    const becameLive = isLive && !isLiveRef.current;
    const itemChanged = itemKeyRef.current !== "" && itemKeyRef.current !== itemKey;
    const addedCount = Math.max(1, itemCount - itemCountRef.current);

    if (switchedRun) {
      runIdRef.current = runId;
      setFollowing(isLive);
      if (startAtLatest) scrollToLatest(false);
      else if (containerRef.current) containerRef.current.scrollTop = 0;
    } else if (becameLive) {
      setFollowing(true);
      scrollToLatest(false);
    } else if (itemChanged && isLive) {
      if (followingRef.current) scrollToLatest(true);
      else setNewEvents((count) => count + addedCount);
    } else if (!isLive && isLiveRef.current) {
      setFollowing(false);
      setNewEvents(0);
    }

    itemKeyRef.current = itemKey;
    itemCountRef.current = itemCount;
    isLiveRef.current = isLive;
  }, [isLive, itemCount, itemKey, runId, scrollToLatest, setFollowing, startAtLatest]);

  const markUserScrollIntent = useCallback(() => {
    programmaticScrollRef.current = false;
    if (releaseTimerRef.current) clearTimeout(releaseTimerRef.current);
  }, []);

  const onScroll = useCallback(() => {
    const container = containerRef.current;
    if (!container || programmaticScrollRef.current || !isLive) return;
    const distanceFromBottom = container.scrollHeight - container.clientHeight - container.scrollTop;
    setFollowing(distanceFromBottom <= BOTTOM_THRESHOLD);
  }, [isLive, setFollowing]);

  const jumpToLatest = useCallback(() => {
    setFollowing(true);
    scrollToLatest(true);
  }, [scrollToLatest, setFollowing]);

  return {
    containerRef,
    following,
    newEvents,
    showJumpToLatest: isLive && !following,
    onScroll,
    markUserScrollIntent,
    jumpToLatest,
  };
}
