import type { NextRequest } from "next/server";
import { proxyReset } from "@/lib/assistantServer";

export async function POST(request: NextRequest) {
  return proxyReset(request);
}
