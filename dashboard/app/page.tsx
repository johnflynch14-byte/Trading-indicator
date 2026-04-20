import SqueezeGrid from "./components/SqueezeGrid";
import SignalFeed from "./components/SignalFeed";
import TradeLog from "./components/TradeLog";

export const dynamic = "force-dynamic";

export default function Home() {
  return (
    <main className="max-w-7xl mx-auto px-4 py-8 space-y-10">

      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold tracking-tight">TTM Squeeze Bot</h1>
          <p className="text-slate-500 text-sm mt-1">Live signal dashboard — updates in real time</p>
        </div>
        <div className="flex items-center gap-2">
          <span className="w-2 h-2 rounded-full bg-emerald-400 animate-pulse" />
          <span className="text-xs text-slate-400">Live</span>
        </div>
      </div>

      <section>
        <h2 className="text-sm font-semibold text-slate-400 uppercase tracking-widest mb-3">
          Watchlist Status
        </h2>
        <SqueezeGrid />
      </section>

      <section>
        <h2 className="text-sm font-semibold text-slate-400 uppercase tracking-widest mb-3">
          Signal Feed
        </h2>
        <SignalFeed />
      </section>

      <section>
        <h2 className="text-sm font-semibold text-slate-400 uppercase tracking-widest mb-3">
          Trade Log
        </h2>
        <TradeLog />
      </section>

    </main>
  );
}
