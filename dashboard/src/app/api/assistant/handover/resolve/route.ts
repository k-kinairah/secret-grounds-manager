import type { NextRequest } from "next/server";
import { proxyHandoverResolve } from "@/lib/assistantServer";

export async function POST(request: NextRequest) {
  return proxyHandoverResolve(request);
}
