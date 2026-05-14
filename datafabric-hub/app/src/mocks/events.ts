import type { LogEvent } from '../types';

export const initialEvents: LogEvent[] = [
  { id: 'e1', time: '14:32', source: 'Stripe', message: 'Nowa płatność #PAY-9921 zsynchronizowana' },
  { id: 'e2', time: '14:31', source: 'SAP', message: 'Opóźnienie odpowiedzi > 800ms' },
  { id: 'e3', time: '14:30', source: 'Kafka', message: 'Konsument dołączył do grupy order-events' },
  { id: 'e4', time: '14:28', source: 'Salesforce', message: 'Pobrano 240 nowych rekordów Account' },
  { id: 'e5', time: '14:27', source: 'GA4', message: 'Sesje w ostatniej godzinie: 1 842' },
  { id: 'e6', time: '14:25', source: 'PostgreSQL', message: 'Snapshot replikacji aktualny (lag 12ms)' },
  { id: 'e7', time: '14:22', source: 'SOAP', message: 'Błąd uwierzytelnienia: token wygasł' },
  { id: 'e8', time: '14:20', source: 'Stripe', message: 'Webhook charge.succeeded — odebrano' },
  { id: 'e9', time: '14:18', source: 'Kafka', message: 'Topic user-events: 3 412 msg/min' },
  { id: 'e10', time: '14:15', source: 'Salesforce', message: 'Mapowanie pola lead_score → priority OK' },
];

export const eventTemplates: Array<{ source: string; message: string }> = [
  { source: 'Stripe', message: 'Nowa płatność zsynchronizowana' },
  { source: 'Stripe', message: 'Webhook charge.succeeded — odebrano' },
  { source: 'Salesforce', message: 'Pobrano nowe rekordy Contact' },
  { source: 'Salesforce', message: 'Aktualizacja Lead → Opportunity' },
  { source: 'PostgreSQL', message: 'Snapshot replikacji aktualny' },
  { source: 'PostgreSQL', message: 'Vacuum auto na tabeli orders' },
  { source: 'SAP', message: 'Opóźnienie odpowiedzi > 600ms' },
  { source: 'SAP', message: 'Synchronizacja BOM ukończona' },
  { source: 'GA4', message: 'Nowa sesja zarejestrowana' },
  { source: 'GA4', message: 'Konwersja purchase odnotowana' },
  { source: 'Kafka', message: 'Partycja przesunięta na nowego brokera' },
  { source: 'Kafka', message: 'Topic user-events: ruch w normie' },
  { source: 'SOAP', message: 'Próba ponowienia połączenia (3/5)' },
];
