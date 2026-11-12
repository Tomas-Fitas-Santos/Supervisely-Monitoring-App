import { useState, type ReactNode } from "react";
import { ArrowDown, ArrowUp, ChevronsUpDown } from "lucide-react";

export type SortValue = string | number | null | undefined;
const collator = new Intl.Collator(undefined, { sensitivity: "base", numeric: true });

export function useTableSort(onChange?: () => void) {
  const [sort, setSort] = useState<{ key: string; direction: "asc" | "desc" }>();
  return {
    query: sort ? "&sort_by=" + encodeURIComponent(sort.key) + "&direction=" + sort.direction : "",
    header(key: string, label: ReactNode) {
      const direction = sort?.key === key ? sort.direction : undefined;
      const Icon = direction === "asc" ? ArrowUp : direction === "desc" ? ArrowDown : ChevronsUpDown;
      return <th key={key} scope="col" aria-sort={direction === "asc" ? "ascending" : direction === "desc" ? "descending" : undefined}>
        <button className="column-sort" type="button" onClick={() => {
          setSort({ key, direction: direction === "asc" ? "desc" : "asc" });
          onChange?.();
        }}>{label}<Icon size={13} aria-hidden="true" /></button>
      </th>;
    },
    rows<T>(rows: T[], value: (row: T, key: string) => SortValue): T[] {
      if (!sort) return rows;
      return [...rows].sort((a, b) => {
        const left = value(a, sort.key);
        const right = value(b, sort.key);
        if (left == null || left === "") return right == null || right === "" ? 0 : 1;
        if (right == null || right === "") return -1;
        const comparison = typeof left === "number" && typeof right === "number"
          ? left - right : collator.compare(String(left), String(right));
        return sort.direction === "asc" ? comparison : -comparison;
      });
    },
  };
}
