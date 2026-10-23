import { notFound } from "next/navigation";
import { AssistantConsole } from "@/components/AssistantConsole";
import { isAssistantEnabled } from "@/lib/assistant";
import styles from "./assistant.module.css";

// Login is enforced by AuthShell (root layout) like every other page; the
// page itself does not exist unless NEXT_PUBLIC_ASSISTANT_ENABLED=true.
export default function AssistantPage() {
  if (!isAssistantEnabled()) {
    notFound();
  }
  return (
    <main className={styles.page}>
      <AssistantConsole />
    </main>
  );
}
