// Mirrors the server's role checks (backend/app/api/dependencies.py). The server enforces them;
// these only decide which controls to show, so nobody is offered a button that can only fail.

const OPERATORS = new Set(["ADMIN", "FINANCE_MANAGER", "OPERATOR"]);
const MANAGERS = new Set(["ADMIN", "FINANCE_MANAGER"]);

/** Execute, stop and escalate cases, record promises, run simulations. */
export const canOperate = (role: string | undefined) => role !== undefined && OPERATORS.has(role);

/** Resolve escalations and edit policy. */
export const canReview = (role: string | undefined) => role !== undefined && MANAGERS.has(role);
