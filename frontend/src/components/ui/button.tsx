import type { ButtonHTMLAttributes } from "react";

type Variant = "primary" | "secondary" | "ghost" | "danger";

const VARIANTS: Record<Variant, string> = {
  primary: "bg-accent text-white hover:bg-accent/90 border-transparent",
  secondary: "bg-surface text-ink hover:bg-accent-soft border-line",
  ghost: "bg-transparent text-ink hover:bg-accent-soft border-transparent",
  danger: "bg-bad text-white hover:bg-bad/90 border-transparent",
};

export type ButtonProps = ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: Variant;
  loading?: boolean;
};

/**
 * One button, four tones, native semantics.
 *
 * While `loading`, the button keeps its place and its focus but refuses input: `aria-busy` tells a
 * screen reader what is happening, and an explicit `disabled` keeps the form from being submitted
 * twice (the demo's top-up form is exactly that case).
 */
export function Button({
  variant = "secondary",
  loading = false,
  disabled,
  className = "",
  children,
  ...rest
}: ButtonProps) {
  return (
    <button
      {...rest}
      disabled={disabled || loading}
      aria-busy={loading || undefined}
      className={`inline-flex items-center justify-center gap-2 rounded-md border px-3.5 py-2 text-sm font-medium transition-colors disabled:cursor-not-allowed disabled:opacity-60 ${VARIANTS[variant]} ${className}`}
    >
      {loading ? <Spinner /> : null}
      {children}
    </button>
  );
}

/** Small enough to live beside the button that uses it; no dependency, no animation library. */
function Spinner() {
  return (
    <span
      aria-hidden="true"
      className="size-3.5 animate-spin rounded-full border-2 border-current border-t-transparent"
    />
  );
}
