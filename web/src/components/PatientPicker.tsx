import { useEffect, useRef, useState } from "react";

/**
 * Type-to-filter patient picker. A plain <select> of 48 ids means scrolling to
 * find the one on the ticket; typing "137" should be enough.
 * Follows the ARIA combobox pattern: the input owns the listbox and reports the
 * highlighted option through aria-activedescendant.
 */
export function PatientPicker({
  patients,
  value,
  onChange,
}: {
  patients: string[];
  value: string;
  onChange: (id: string) => void;
}) {
  const [query, setQuery] = useState("");
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(0);
  const box = useRef<HTMLDivElement | null>(null);

  const matches = query.trim()
    ? patients.filter((id) => id.toLowerCase().includes(query.trim().toLowerCase()))
    : patients;
  // Only the rendered slice is navigable, so the active index always maps to a
  // real <li> and aria-activedescendant can never dangle.
  const visible = matches.slice(0, 60);

  // Clicking anywhere else closes the list and drops a half-typed filter.
  useEffect(() => {
    function onDocClick(event: MouseEvent) {
      if (box.current && !box.current.contains(event.target as Node)) {
        setOpen(false);
        setQuery("");
      }
    }
    document.addEventListener("mousedown", onDocClick);
    return () => document.removeEventListener("mousedown", onDocClick);
  }, []);

  function choose(id: string) {
    onChange(id);
    setOpen(false);
    setQuery("");
  }

  function onKeyDown(event: React.KeyboardEvent) {
    if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      event.preventDefault();
      if (!open) {
        setOpen(true);
        return;
      }
      const step = event.key === "ArrowDown" ? 1 : -1;
      setActive((current) => {
        const next = current + step;
        if (next < 0) return visible.length - 1;
        if (next >= visible.length) return 0;
        return next;
      });
      return;
    }
    if (event.key === "Enter" && open && visible[active]) {
      event.preventDefault();
      choose(visible[active]);
      return;
    }
    if (event.key === "Escape") {
      setOpen(false);
      setQuery("");
    }
  }

  return (
    <div className="field">
      <label className="label" htmlFor="patient-input">
        Patient
      </label>
      <p className="hint" id="patient-hint">
        Type any part of the id to filter. {patients.length} on file.
      </p>
      <div className="combo" ref={box}>
        <input
          id="patient-input"
          type="text"
          role="combobox"
          autoComplete="off"
          aria-expanded={open}
          aria-controls="patient-listbox"
          aria-describedby="patient-hint"
          aria-autocomplete="list"
          aria-activedescendant={open && visible[active] ? `patient-opt-${visible[active]}` : undefined}
          value={open ? query : value}
          placeholder={value || "PT00137"}
          onChange={(event) => {
            setQuery(event.target.value);
            setActive(0);
            setOpen(true);
          }}
          onFocus={() => setOpen(true)}
          onKeyDown={onKeyDown}
          style={{ fontFamily: "var(--mono)" }}
        />
        {open && (
          <ul className="combo-list" role="listbox" id="patient-listbox" aria-label="Patients">
            {visible.length === 0 && <li className="combo-empty">No patient id matches that.</li>}
            {visible.map((id, index) => (
              <li
                key={id}
                id={`patient-opt-${id}`}
                role="option"
                aria-selected={id === value}
                className={index === active ? "combo-option is-active" : "combo-option"}
                onMouseEnter={() => setActive(index)}
                onMouseDown={(event) => {
                  event.preventDefault();
                  choose(id);
                }}
              >
                {id}
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  );
}
