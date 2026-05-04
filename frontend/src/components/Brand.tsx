type CernoMarkProps = {
  className?: string;
};

type CernoWordmarkProps = {
  className?: string;
};

type CernoLockupProps = {
  className?: string;
  markClassName?: string;
  wordmarkClassName?: string;
};

export function CernoMark({ className = "h-7 w-7" }: CernoMarkProps) {
  return (
    <img
      src="/brand/cerno-mark.svg"
      alt=""
      aria-hidden="true"
      className={className}
      draggable={false}
    />
  );
}

export function CernoWordmark({ className = "" }: CernoWordmarkProps) {
  return <span className={`cerno-wordmark ${className}`}>Cerno</span>;
}

export function CernoLockup({
  className = "",
  markClassName = "h-7 w-7",
  wordmarkClassName = "text-sm",
}: CernoLockupProps) {
  return (
    <span className={`inline-flex items-center gap-2 ${className}`}>
      <CernoMark className={markClassName} />
      <CernoWordmark className={wordmarkClassName} />
    </span>
  );
}
