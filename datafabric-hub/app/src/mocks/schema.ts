import type { SchemaEntity } from '../types';

export const schema: SchemaEntity[] = [
  {
    id: 'USR',
    name: 'User',
    fields: 'id, email, name, created_at',
    sources: ['CRM', 'PG'],
  },
  {
    id: 'ORD',
    name: 'Order',
    fields: 'id, user_id, amount, status',
    sources: ['Stripe', 'SAP'],
  },
  {
    id: 'EVT',
    name: 'Event',
    fields: 'type, session_id, page, ts',
    sources: ['GA4', 'Kafka'],
  },
];

export const schemaVersion = '3.2.1';
