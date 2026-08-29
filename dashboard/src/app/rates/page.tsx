import { redirect } from "next/navigation";

// The standalone Rates page has been retired. Rate/pricing configuration now
// lives under Admin -> Settings -> Rates & Pricing. This redirect keeps old
// bookmarks and links from 404ing -- any client hitting /rates is sent
// straight to /settings.
export default function RatesPage() {
  redirect("/settings");
}
