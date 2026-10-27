import { redirect } from "next/navigation";

/**
 * `/staff` has no screen of its own: the console opens on the customer directory, which is the entry
 * point of the story this demo tells (§5 of the plan).
 */
export default function StaffIndex() {
  redirect("/staff/customers");
}
