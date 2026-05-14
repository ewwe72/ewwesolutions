import { useEffect } from 'react';
import { Topbar } from './components/Topbar';
import { Sidebar } from './components/Sidebar';
import { SourceList } from './components/SourceList';
import { HourlySparkline } from './components/HourlySparkline';
import { VolumeBySource } from './components/VolumeBySource';
import { UnifiedSchema } from './components/UnifiedSchema';
import { EventLog } from './components/EventLog';
import { useHubStore } from './store/useHubStore';

function App() {
  const tick = useHubStore((s) => s.tick);

  useEffect(() => {
    const id = setInterval(tick, 3000);
    return () => clearInterval(id);
  }, [tick]);

  return (
    <div className="min-h-screen flex flex-col bg-[#0f1115] text-gray-200">
      <Topbar />
      <div className="flex flex-1 min-h-0">
        <Sidebar />
        <main className="flex-1 p-5 overflow-auto">
          <div className="grid grid-cols-1 lg:grid-cols-[1.6fr_1fr] gap-4 max-w-[1400px] mx-auto">
            <SourceList />
            <div className="space-y-4">
              <HourlySparkline />
              <VolumeBySource />
            </div>
            <UnifiedSchema />
            <EventLog />
          </div>
        </main>
      </div>
    </div>
  );
}

export default App;
