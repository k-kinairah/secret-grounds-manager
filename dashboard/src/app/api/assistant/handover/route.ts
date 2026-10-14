import type { NextRequest } from "next/server";
import { proxyHandoverList } from "@/lib/assistantServer";

export async function GET(request: NextRequest) {
  return proxyHandoverList(request);
}
