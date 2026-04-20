"use client";

import { useEffect, useState } from "react";
import { supabase, Signal } from "../lib/supabase";

function timeAgo(iso: string) {
  const diff = Date.now() - new Date(iso).getTime();
  const m = Math.floor(diff / 60000);
  if (m < 1) return "just now";
  if (m < 60) return `${m}m ago`;
  const h = Math.floor(m / 60);
  if (h < 24) return `${h}h ago`;
  return `${Math.floor(h / 24)}d ago`;
}

export default function SignalFeed() {
  const [signals, setSignals] = useState<Signal[]>([]);

  useEffect(() => {
    supabase
      .from("signals")
      .select("*")
      .order("created_at", { ascending: false })
      .limit(50)
      .then(({ data }) => { if (data) setSignals(data); });

    const channel = supabase
      .channel("signals_feed")
      .on("postgres_changes", { event: "INSERT", schema: "public", table: "signals" },
        (payload) => {
          setSignals((prev) => [payload.new as Signal, ...prev].slice(0, 50));
        }
      )
      .subscribe();

    return () => { supabase.removeChannel(channel); };
  }, []);

  if (signals.length === 0)
    return <p className="text-slate-500 text-sm">No signals yet — bot will populate this on the next squeeze fire.</p>;

  return (
    <div className="space-y-3">
      {signals.map((s) => (
        <div
          key={s.id}
          className={`rounded-lg border p-4 ${
            s.direction === "BULLISH"
              ? "border-emerald-700 bg-emerald-950/40"
              : "border-rose-700 bg-rose-950/40"
          }`}
        >
          <div className="flex justify-between items-center mb-2">
            <div className="flex items-center gap-3">
              <span className="font-bold text-base">{s.symbol}</span>
              <span className={`text-xs font-bold px-2 py-0.5 rounded ${
                s.direction === "BULLISH"
                  ? "bg-emerald-500/20 text-emerald-400"
                  : "bg-rose-500/20 text-rose-400"
              }`}>
                {s.direction === "BULLISH" ? "▲ BULLISH" : "▼ BEARISH"}
              </span>
              {s.sim_mode && (
                <span className="text-xs text-slate-500 bg-slate-800 px-2 py-0.5 rounded">SIM</span>
              )}
            </div>
            <span className="text-xs text-slate-500">{timeAgo(s.created_at)}</span>
          </div>

          <div className="flex gap-4 text-xs text-slate-400 mb-3">
            <span>close <span className="text-slate-200">${s.close.toFixed(2)}</span></span>
            <span>mom <span className={s.momentum >= 0 ? "text-emerald-400" : "text-rose-400"}>
              {s.momentum >= 0 ? "+" : ""}{s.momentum.toFixed(4)}
            </span></span>
            <span>BB {s.bb_lower.toFixed(1)}–{s.bb_upper.toFixed(1)}</span>
            <span>KC {s.kc_lower.toFixed(1)}–{s.kc_upper.toFixed(1)}</span>
          </div>

          <div className="text-sm text-slate-300 leading-relaxed border-t border-slate-700 pt-3">
            {s.claude_analysis}
          </div>
        </div>
      ))}
    </div>
  );
}
