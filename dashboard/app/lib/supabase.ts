import { createClient } from "@supabase/supabase-js";

export const supabase = createClient(
  process.env.NEXT_PUBLIC_SUPABASE_URL!,
  process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY!
);

export type Signal = {
  id: string;
  symbol: string;
  direction: "BULLISH" | "BEARISH";
  close: number;
  momentum: number;
  momentum_prev: number;
  bb_upper: number;
  bb_lower: number;
  kc_upper: number;
  kc_lower: number;
  claude_analysis: string;
  sim_mode: boolean;
  created_at: string;
};

export type Trade = {
  id: string;
  symbol: string;
  side: string;
  quantity: number;
  price: number;
  order_id: string;
  sim_mode: boolean;
  created_at: string;
};

export type SqueezeStatus = {
  symbol: string;
  squeeze_on: boolean;
  momentum: number;
  close: number;
  direction: "BULLISH" | "BEARISH" | null;
  updated_at: string;
};
