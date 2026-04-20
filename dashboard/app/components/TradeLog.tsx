"use client";

import { useEffect, useState } from "react";
import { supabase, Trade } from "../lib/supabase";

export default function TradeLog() {
  const [trades, setTrades] = useState<Trade[]>([]);

  useEffect(() => {
    supabase
      .from("trades")
      .select("*")
      .order("created_at", { ascending: false })
      .limit(100)
      .then(({ data }) => { if (data) setTrades(data); });

    const channel = supabase
      .channel("trades_feed")
      .on("postgres_changes", { event: "INSERT", schema: "public", table: "trades" },
        (payload) => {
          setTrades((prev) => [payload.new as Trade, ...prev].slice(0, 100));
        }
      )
      .subscribe();

    return () => { supabase.removeChannel(channel); };
  }, []);

  if (trades.length === 0)
    return <p className="text-slate-500 text-sm">No trades placed yet.</p>;

  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm">
        <thead>
          <tr className="text-slate-500 text-xs border-b border-slate-800">
            <th className="text-left pb-2 pr-4">Time</th>
            <th className="text-left pb-2 pr-4">Symbol</th>
            <th className="text-left pb-2 pr-4">Side</th>
            <th className="text-right pb-2 pr-4">Qty</th>
            <th className="text-right pb-2 pr-4">Price</th>
            <th className="text-left pb-2">Order ID</th>
          </tr>
        </thead>
        <tbody>
          {trades.map((t) => (
            <tr key={t.id} className="border-b border-slate-800/50">
              <td className="py-2 pr-4 text-slate-500 text-xs">
                {new Date(t.created_at).toLocaleString()}
              </td>
              <td className="py-2 pr-4 font-bold">{t.symbol}</td>
              <td className={`py-2 pr-4 font-bold ${
                t.side === "buy" ? "text-emerald-400" : "text-rose-400"
              }`}>
                {t.side === "buy" ? "BUY" : "SHORT"}
              </td>
              <td className="py-2 pr-4 text-right">{t.quantity}</td>
              <td className="py-2 pr-4 text-right">${t.price?.toFixed(2) ?? "—"}</td>
              <td className="py-2 text-slate-500 text-xs">
                {t.sim_mode ? <span className="text-slate-600">[sim]</span> : (t.order_id || "—")}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
