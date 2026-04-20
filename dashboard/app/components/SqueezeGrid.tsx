"use client";

import { useEffect, useState } from "react";
import { supabase, SqueezeStatus } from "../lib/supabase";

export default function SqueezeGrid() {
  const [statuses, setStatuses] = useState<SqueezeStatus[]>([]);

  useEffect(() => {
    supabase
      .from("squeeze_status")
      .select("*")
      .order("symbol")
      .then(({ data }) => { if (data) setStatuses(data); });

    const channel = supabase
      .channel("squeeze_status_changes")
      .on("postgres_changes", { event: "*", schema: "public", table: "squeeze_status" },
        (payload) => {
          setStatuses((prev) => {
            const updated = payload.new as SqueezeStatus;
            const idx = prev.findIndex((s) => s.symbol === updated.symbol);
            if (idx >= 0) {
              const next = [...prev];
              next[idx] = updated;
              return next;
            }
            return [...prev, updated].sort((a, b) => a.symbol.localeCompare(b.symbol));
          });
        }
      )
      .subscribe();

    return () => { supabase.removeChannel(channel); };
  }, []);

  if (statuses.length === 0)
    return <p className="text-slate-500 text-sm">Waiting for first scan…</p>;

  return (
    <div className="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-4 lg:grid-cols-6 gap-2">
      {statuses.map((s) => {
        const fired = s.direction !== null;
        const bg = fired
          ? s.direction === "BULLISH" ? "bg-emerald-900/60 border-emerald-500" : "bg-rose-900/60 border-rose-500"
          : s.squeeze_on ? "bg-yellow-900/40 border-yellow-600" : "bg-slate-800/60 border-slate-700";

        return (
          <div key={s.symbol} className={`rounded-lg border p-3 ${bg}`}>
            <div className="flex justify-between items-start">
              <span className="font-bold text-sm">{s.symbol}</span>
              {s.squeeze_on && !fired && (
                <span className="text-yellow-400 text-xs">COILING</span>
              )}
              {fired && (
                <span className={`text-xs font-bold ${s.direction === "BULLISH" ? "text-emerald-400" : "text-rose-400"}`}>
                  {s.direction === "BULLISH" ? "▲ FIRE" : "▼ FIRE"}
                </span>
              )}
            </div>
            <div className="mt-1 text-xs text-slate-400">${s.close.toFixed(2)}</div>
            <div className={`text-xs mt-1 ${s.momentum >= 0 ? "text-emerald-400" : "text-rose-400"}`}>
              mom {s.momentum >= 0 ? "+" : ""}{s.momentum.toFixed(3)}
            </div>
          </div>
        );
      })}
    </div>
  );
}
