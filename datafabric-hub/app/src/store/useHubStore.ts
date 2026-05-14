import { create } from 'zustand';
import type { LogEvent, Source } from '../types';
import { sources as initialSources } from '../mocks/sources';
import { initialEvents, eventTemplates } from '../mocks/events';

interface HubState {
  sources: Source[];
  events: LogEvent[];
  filterSourceId: string | null;
  setFilter: (id: string | null) => void;
  toggleFilter: (id: string) => void;
  tick: () => void;
}

let counter = 1000;

function nextTime(): string {
  const d = new Date();
  return `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`;
}

export const useHubStore = create<HubState>((set) => ({
  sources: initialSources,
  events: initialEvents,
  filterSourceId: null,
  setFilter: (id) => set({ filterSourceId: id }),
  toggleFilter: (id) =>
    set((s) => ({ filterSourceId: s.filterSourceId === id ? null : id })),
  tick: () =>
    set((s) => {
      const tpl = eventTemplates[Math.floor(Math.random() * eventTemplates.length)];
      const newEvent: LogEvent = {
        id: `e${++counter}`,
        time: nextTime(),
        source: tpl.source,
        message: tpl.message,
      };
      const updatedSources = s.sources.map((src) => {
        if (src.count === null) return src;
        if (src.shortLabel === tpl.source || src.name.startsWith(tpl.source)) {
          return { ...src, count: src.count + Math.floor(Math.random() * 9 + 1) };
        }
        return src;
      });
      return {
        events: [newEvent, ...s.events].slice(0, 30),
        sources: updatedSources,
      };
    }),
}));
