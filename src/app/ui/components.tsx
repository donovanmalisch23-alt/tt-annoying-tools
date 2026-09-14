import type { ReactElement, ReactNode } from "react";

/** Small hand-rolled primitives, so the panel has one consistent look. */

export function Panel({
  title,
  subtitle,
  actions,
  children,
  tone = "default",
}: {
  title?: string;
  subtitle?: string;
  actions?: ReactNode;
  children: ReactNode;
  tone?: "default" | "danger";
}): ReactElement {
  return (
    <section className={`panel${tone === "danger" ? " panel--danger" : ""}`}>
      {(title || actions) && (
        <header className="panel__head">
          <div>
            {title && <h2 className="panel__title">{title}</h2>}
            {subtitle && <p className="panel__subtitle">{subtitle}</p>}
          </div>
          {actions && <div className="panel__actions">{actions}</div>}
        </header>
      )}
      <div className="panel__body">{children}</div>
    </section>
  );
}

export function Button({
  children,
  onClick,
  variant = "default",
  disabled,
  size = "md",
  type = "button",
  title,
}: {
  children: ReactNode;
  onClick?: () => void;
  variant?: "default" | "primary" | "danger" | "ghost";
  disabled?: boolean;
  size?: "sm" | "md";
  type?: "button" | "submit";
  title?: string;
}): ReactElement {
  return (
    <button
      type={type}
      className={`btn btn--${variant} btn--${size}`}
      onClick={onClick}
      disabled={disabled}
      title={title}
    >
      {children}
    </button>
  );
}

export function Badge({
  children,
  tone = "muted",
}: {
  children: ReactNode;
  tone?: "ok" | "warn" | "danger" | "muted" | "accent";
}): ReactElement {
  return <span className={`badge badge--${tone}`}>{children}</span>;
}

export function Stat({
  label,
  value,
  tone = "default",
  hint,
}: {
  label: string;
  value: ReactNode;
  tone?: "default" | "ok" | "warn" | "danger" | "accent";
  hint?: string;
}): ReactElement {
  return (
    <div className={`stat stat--${tone}`}>
      <span className="stat__label">{label}</span>
      <span className="stat__value">{value}</span>
      {hint && <span className="stat__hint">{hint}</span>}
    </div>
  );
}

export function Meter({
  value,
  max,
  tone = "accent",
  label,
}: {
  value: number;
  max: number;
  tone?: "accent" | "warn" | "danger";
  label?: string;
}): ReactElement {
  const ratio = max <= 0 ? 0 : Math.min(1, Math.max(0, value / max));
  return (
    <div className="meter">
      <div className="meter__track">
        <div className={`meter__fill meter__fill--${tone}`} style={{ width: `${ratio * 100}%` }} />
      </div>
      {label && <span className="meter__label">{label}</span>}
    </div>
  );
}

export function Field({
  label,
  help,
  children,
}: {
  label: string;
  help?: string;
  children: ReactNode;
}): ReactElement {
  return (
    <label className="field">
      <span className="field__label">{label}</span>
      {children}
      {help && <span className="field__help">{help}</span>}
    </label>
  );
}

export function TextInput({
  value,
  onChange,
  type = "text",
  placeholder,
  min,
  max,
}: {
  value: string;
  onChange: (value: string) => void;
  type?: string;
  placeholder?: string;
  min?: number;
  max?: number;
}): ReactElement {
  return (
    <input
      className="input"
      type={type}
      value={value}
      placeholder={placeholder}
      min={min}
      max={max}
      onChange={(event) => onChange(event.target.value)}
    />
  );
}

export function TextArea({
  value,
  onChange,
  rows = 4,
  placeholder,
  mono,
}: {
  value: string;
  onChange: (value: string) => void;
  rows?: number;
  placeholder?: string;
  mono?: boolean;
}): ReactElement {
  return (
    <textarea
      className={`input input--area${mono ? " input--mono" : ""}`}
      rows={rows}
      value={value}
      placeholder={placeholder}
      onChange={(event) => onChange(event.target.value)}
    />
  );
}

export function Select({
  value,
  onChange,
  options,
}: {
  value: string;
  onChange: (value: string) => void;
  options: string[];
}): ReactElement {
  return (
    <select className="input" value={value} onChange={(event) => onChange(event.target.value)}>
      {options.map((option) => (
        <option key={option} value={option}>
          {option}
        </option>
      ))}
    </select>
  );
}

export function Toggle({
  checked,
  onChange,
  label,
}: {
  checked: boolean;
  onChange: (checked: boolean) => void;
  label: string;
}): ReactElement {
  return (
    <button
      type="button"
      className={`toggle${checked ? " toggle--on" : ""}`}
      onClick={() => onChange(!checked)}
      aria-pressed={checked}
    >
      <span className="toggle__knob" />
      <span className="toggle__label">{label}</span>
    </button>
  );
}

export function Empty({ children }: { children: ReactNode }): ReactElement {
  return <p className="empty">{children}</p>;
}
