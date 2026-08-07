import React, { useRef, useState, useEffect } from "react";

interface BBox {
  x: number;
  y: number;
  width: number;
  height: number;
}

interface Issue {
  category: string;
  type: string;
  element: string;
  selector: string;
  refValue: string;
  liveValue: string;
  boundingBox: BBox | null;
  matchedBy: string | null;
}

interface ScreenshotOverlayProps {
  imageSrc: string;
  issues: Issue[];
  column: "reference" | "live";
  hoveredIssueKey: string | null;
  onHoverIssue: (key: string | null) => void;
}

export const ScreenshotOverlay: React.FC<ScreenshotOverlayProps> = ({
  imageSrc,
  issues,
  column,
  hoveredIssueKey,
  onHoverIssue,
}) => {
  const containerRef = useRef<HTMLDivElement>(null);
  const imgRef = useRef<HTMLImageElement>(null);

  const [imgSize, setImgSize] = useState({
    naturalWidth: 1,
    naturalHeight: 1,
    clientWidth: 1,
    clientHeight: 1,
  });

  const updateSize = () => {
    if (imgRef.current) {
      setImgSize({
        naturalWidth: imgRef.current.naturalWidth || 1,
        naturalHeight: imgRef.current.naturalHeight || 1,
        clientWidth: imgRef.current.clientWidth || 1,
        clientHeight: imgRef.current.clientHeight || 1,
      });
    }
  };

  // Update size when image loads or window resizes
  useEffect(() => {
    window.addEventListener("resize", updateSize);
    return () => window.removeEventListener("resize", updateSize);
  }, []);

  const handleImageLoad = () => {
    updateSize();
  };

  // Re-run size check when image source changes
  useEffect(() => {
    if (imgRef.current && imgRef.current.complete) {
      updateSize();
    }
  }, [imageSrc]);

  const scaleX = imgSize.clientWidth / imgSize.naturalWidth;
  const scaleY = imgSize.clientHeight / imgSize.naturalHeight;

  // Filter issues depending on column type:
  // - Reference column: displays only 'missing' issues
  // - Live column: displays 'extra' and mismatches (content-mismatch, attribute-mismatch, etc.)
  const visibleIssues = issues.filter((issue) => {
    if (!issue.boundingBox) return false;
    if (column === "reference") {
      return issue.type === "missing";
    } else {
      return issue.type !== "missing";
    }
  });

  return (
    <div
      ref={containerRef}
      className="relative w-full rounded-lg overflow-hidden border border-slate-700 bg-slate-900 shadow-xl"
      style={{ minHeight: "200px" }}
    >
      <img
        ref={imgRef}
        src={imageSrc ? `http://localhost:8000${imageSrc}` : ""}
        alt={`${column} Page View`}
        className="w-full h-auto block select-none"
        onLoad={handleImageLoad}
        crossOrigin="anonymous"
      />
      
      {/* Absolute Overlay Box Container */}
      <div className="absolute inset-0 pointer-events-none">
        {visibleIssues.map((issue, idx) => {
          const bbox = issue.boundingBox!;
          const key = `${issue.selector}-${issue.type}-${issue.category}-${idx}`;
          const isHovered = hoveredIssueKey === key;

          const left = bbox.x * scaleX;
          const top = bbox.y * scaleY;
          const width = bbox.width * scaleX;
          const height = bbox.height * scaleY;

          // Short label formatting
          let shortLabel = issue.type.replace("-", " ").toUpperCase();
          if (issue.element) {
            shortLabel += ` (${issue.element})`;
          }

          return (
            <div
              key={key}
              className="absolute pointer-events-auto transition-all duration-150"
              style={{
                left: `${left}px`,
                top: `${top}px`,
                width: `${width}px`,
                height: `${height}px`,
                border: isHovered ? "3px solid #f43f5e" : "2px solid #ef4444",
                backgroundColor: isHovered ? "rgba(244, 63, 94, 0.15)" : "rgba(239, 68, 68, 0.05)",
                zIndex: isHovered ? 30 : 10,
                cursor: "pointer",
              }}
              onMouseEnter={() => onHoverIssue(key)}
              onMouseLeave={() => onHoverIssue(null)}
            >
              {/* Floating issue tag */}
              <div
                className={`absolute px-1.5 py-0.5 text-[10px] font-bold rounded shadow-lg select-none truncate pointer-events-none transition-opacity ${
                  isHovered ? "opacity-100 scale-105" : "opacity-75"
                }`}
                style={{
                  top: top > 24 ? "-22px" : "2px",
                  left: "0",
                  backgroundColor: isHovered ? "#f43f5e" : "#ef4444",
                  color: "#ffffff",
                  maxWidth: "140px",
                }}
                title={shortLabel}
              >
                {shortLabel}
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
};
export default ScreenshotOverlay;
