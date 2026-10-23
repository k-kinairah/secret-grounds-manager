import type { NextRequest } from "next/server";
import { proxyConfirm } from "@/lib/assistantServer";

export async function POST(request: NextRequest) {
  return proxyConfirm(request);
}
