export type SourceStatus = 'ok' | 'sync' | 'lag' | 'błąd' | 'live';

export interface Source {
  id: string;
  name: string;
  protocol: string;
  count: number | null;
  status: SourceStatus;
  dot: string;
  volume: number;
  barColor: string;
  shortLabel: string;
}

export interface SchemaEntity {
  id: 'USR' | 'ORD' | 'EVT';
  name: string;
  fields: string;
  sources: string[];
}

export interface LogEvent {
  id: string;
  time: string;
  source: string;
  message: string;
}

export interface NavItem {
  id: string;
  label: string;
  dot?: string;
}
