import { cva, type VariantProps } from "class-variance-authority";
import type { ButtonHTMLAttributes, Ref } from "react";
import { cn } from "../../lib/cn";

const buttonVariants = cva(
  "inline-flex items-center justify-center gap-2 rounded-lg text-sm font-medium transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-indigo/40 disabled:pointer-events-none disabled:opacity-50",
  {
    variants: {
      variant: {
        primary: "bg-indigo px-4 py-2.5 text-white hover:bg-[#484b93]",
        secondary: "border border-line bg-panel px-4 py-2.5 text-ink hover:bg-white",
        ghost: "px-3 py-2 text-muted hover:bg-black/[0.04] hover:text-ink",
      },
    },
    defaultVariants: { variant: "primary" },
  },
);

export type ButtonProps = ButtonHTMLAttributes<HTMLButtonElement> & VariantProps<typeof buttonVariants> & { ref?: Ref<HTMLButtonElement> };

export function Button({ className, variant, ...props }: ButtonProps) {
  return <button className={cn(buttonVariants({ variant }), className)} {...props} />;
}
