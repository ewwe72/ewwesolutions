# DataFabric Hub — Specyfikacja

## 1. Cel

Zbudować jednostronicowy panel (SPA) demonstrujący narzędzie typu **API hub / data fabric**: integracja wielu rozproszonych źródeł danych (CRM, bazy, API, streamy, webhooki) z prezentacją statusu, wolumenu i ujednoliconego schematu w jednym widoku.

Punkt odniesienia wizualny: załączony mockup `DataFabric Hub`. Implementacja ma odtworzyć ten widok i być rozszerzalna o realne integracje.

**Pierwszy milestone = demo front-end z mockowanymi danymi.** Backend i realne konektory są poza zakresem MS1.

## 2. Stack

- **Vite + React 18 + TypeScript** (SPA, bez SSR — to ma być demo, nie produkt)
- **Tailwind CSS** dla stylów (dark mode domyślnie)
- **Zustand** dla globalnego stanu (źródła, eventy, schema) — nie Redux
- **Recharts** dla wykresów (sparkline godzinowy, bar chart wolumenu)
- **lucide-react** dla ikon
- Brak backendu w MS1. Dane z `src/mocks/*.ts` + symulowane ticki przez `setInterval`.
- Język interfejsu: **polski**. Stringi w jednym pliku `src/i18n/pl.ts`, żeby łatwo przepiąć na i18n później.

## 3. Layout

Trzy strefy, układ jak w mockupie:

```
┌─────────────────────────────────────────────────────┐
│  Topbar: logo, zakładki, status, menu              │
├──────────┬──────────────────────────────────────────┤
│          │  ┌─────────────────┐  ┌──────────────┐  │
│ Sidebar  │  │ Lista źródeł    │  │ Sparkline    │  │
│  ─       │  │ (karty)         │  ├──────────────┤  │
│ menu     │  │                 │  │ Wolumen wg   │  │
│ sekcje   │  │                 │  │ źródła       │  │
│          │  └─────────────────┘  └──────────────┘  │
│          │  ┌─────────────────┐  ┌──────────────┐  │
│          │  │ Ujednolicony    │  │ Dziennik     │  │
│          │  │ schemat danych  │  │ zdarzeń      │  │
│          │  └─────────────────┘  └──────────────┘  │
└──────────┴──────────────────────────────────────────┘
```

- **Topbar**: tytuł `DataFabric Hub`, zakładki `Przegląd | Mapowanie | Przepływy | Alerty | Ustawienia` (tylko `Przegląd` aktywny w MS1), badge `● na żywo`, badge `2 ostrzeżenia`, menu `…`.
- **Sidebar (szer. ~200px)**: nagłówek `ŹRÓDŁA DANYCH` (kropki kolorów statusu + nazwa), nagłówek `TRANSFORMACJE` (`Normalizacja`, `Deduplikacja`, `Mapowanie pól` (aktywne), `Walidacja`).
- **Main grid**: 2 kolumny, lewa szersza (~60%), prawa węższa.

## 4. Komponenty

Każdy komponent w osobnym pliku w `src/components/`:

- `Topbar.tsx`
- `Sidebar.tsx`
- `SourceList.tsx` — lista kart źródeł
- `SourceCard.tsx` — pojedyncza karta (nazwa + protokół, licznik, status pill)
- `HourlySparkline.tsx` — mini bar chart 00:00 / 12:00 / teraz
- `VolumeByPSource.tsx` — lista źródeł z poziomymi paskami + wartości
- `UnifiedSchema.tsx` — bloki encji `User`, `Order`, `Event` z polami i tagami źródeł
- `EventLog.tsx` — pionowa lista zdarzeń (czas, źródło, opis)

## 5. Model danych (mock)

```ts
// src/types.ts
export type SourceStatus = 'ok' | 'sync' | 'lag' | 'błąd' | 'live';

export interface Source {
  id: string;
  name: string;          // "Salesforce CRM"
  protocol: string;      // "REST API · OAuth2"
  count: number | null;  // null => "—" (np. dla błąd)
  status: SourceStatus;
  color: string;         // tailwind class, np. "bg-emerald-500"
}

export interface SchemaEntity {
  id: 'USR' | 'ORD' | 'EVT';
  name: string;
  fields: string[];
  sources: string[];     // tagi: ["CRM", "PG"]
}

export interface LogEvent {
  id: string;
  time: string;          // "14:32"
  source: string;        // "Stripe"
  message: string;
}
```

Mocki: 7 źródeł zgodnie z mockupem (Salesforce, PostgreSQL, Stripe, SAP ERP, Legacy SOAP, Google Analytics, Kafka), 3 encje schematu, ~10 zdarzeń startowych.

## 6. Zachowania (MS1)

- **Tick co 3s**: losowo wybrane źródło dostaje nowy event w logu (prepend, max 30 elementów). Liczniki źródeł rosną proporcjonalnie do wolumenu.
- **Status pills mają stałe kolory**: `ok`=zielony, `sync`=niebieski, `lag`=żółty, `błąd`=czerwony, `live`=fioletowy.
- **Sparkline** generowany z 24 słupków, ostatni 30% wyższy (efekt „rośnie do teraz").
- **Hover na karcie źródła** — subtelne podświetlenie tła.
- **Klik na karcie źródła** — filtruje `EventLog` po tym źródle (toggle). Stan filtra w Zustand.
- **Sidebar** — pozycje nieaktywne pokazują tylko hover, brak nawigacji.

Brak routingu w MS1. Brak realnych zapytań sieciowych.

## 7. Styl

- Tło główne: `#0f1115` (prawie czarne)
- Karty: `#1a1d24` z border `#262a33`
- Tekst główny: `#e5e7eb`, secondary: `#9ca3af`
- Akcent zakładki aktywnej: zielona linia pod tekstem
- Font: system stack (`ui-sans-serif`)
- Zaokrąglenia: `rounded-xl` dla kart, `rounded-full` dla pill
- Brak gradientów, brak shadow-2xl. Płaskie, gęste, czytelne.

## 8. Struktura plików

```
src/
  components/
    Topbar.tsx
    Sidebar.tsx
    SourceList.tsx
    SourceCard.tsx
    HourlySparkline.tsx
    VolumeBySource.tsx
    UnifiedSchema.tsx
    EventLog.tsx
  mocks/
    sources.ts
    schema.ts
    events.ts
  store/
    useHubStore.ts
  i18n/
    pl.ts
  types.ts
  App.tsx
  main.tsx
  index.css
```

## 9. Kryteria akceptacji MS1

- [ ] `npm run dev` startuje bez błędów, otwiera się panel
- [ ] Wszystkie 4 sekcje main + sidebar + topbar widoczne i wypełnione
- [ ] Lista 7 źródeł z odpowiednimi statusami i licznikami zgadza się z mockupem
- [ ] Event log dopisuje nowe wpisy co ~3s
- [ ] Klik w źródło filtruje log
- [ ] `npm run build` przechodzi bez warningów TS
- [ ] Brak `any`, brak `// @ts-ignore`
- [ ] Lighthouse a11y ≥ 90 (kontrasty, role aria gdzie sensowne)

## 10. Poza zakresem (świadomie)

- Realne konektory do źródeł (OAuth, JDBC, SOAP, Kafka)
- Persystencja, baza, auth
- Pozostałe zakładki (`Mapowanie`, `Przepływy`, `Alerty`, `Ustawienia`)
- Mobile / responsive (panel zakłada szer. ≥ 1100px, na mniejszych — graceful overflow, nie pełna adaptacja)
- Testy E2E. Jednostkowe tylko dla `useHubStore` (reducer logic).

## 11. Co dalej (MS2+, do późniejszej dyskusji)

- Realny backend (Node + Fastify), per-source adaptery
- WebSocket dla event logu zamiast `setInterval`
- Strona `Mapowanie pól` — drag & drop pól źródłowych na encje unified schema
- Alerty z regułami (np. `lag > 500ms przez 5 min`)
- Eksport schematu jako JSON Schema / OpenAPI

---

**Uruchomienie pracy przez nową instancję:**
1. Zainicjuj projekt: `npm create vite@latest datafabric-hub -- --template react-ts`
2. Zainstaluj zależności z sekcji 2
3. Skonfiguruj Tailwind (dark mode `class`, `darkMode: 'class'`)
4. Buduj komponent po komponencie w kolejności z sekcji 4 — po każdym sprawdź wizualnie w przeglądarce
5. Na końcu uruchom checki z sekcji 9
