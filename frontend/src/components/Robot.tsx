export default function Robot() {
  return (
    <div className="robot" aria-hidden="true">
      <div className="robot-bubble">
        Xin chào! <span>👋</span>
        <br />
        Cần tìm gì không?
      </div>
      <svg
        className="robot-art"
        viewBox="0 0 360 300"
        fill="none"
        xmlns="http://www.w3.org/2000/svg"
      >
        <defs>
          <linearGradient id="bot-shell" x1="110" y1="60" x2="250" y2="250">
            <stop stopColor="#FFFFFF" />
            <stop offset="0.55" stopColor="#F1F2F8" />
            <stop offset="1" stopColor="#D9DCEC" />
          </linearGradient>
          <linearGradient id="bot-face" x1="130" y1="95" x2="230" y2="175">
            <stop stopColor="#232532" />
            <stop offset="1" stopColor="#0E0F15" />
          </linearGradient>
          <linearGradient id="bot-glow" x1="150" y1="110" x2="210" y2="165">
            <stop stopColor="#9DEBFF" />
            <stop offset="1" stopColor="#57C8F5" />
          </linearGradient>
          <radialGradient id="bot-shadow" cx="0.5" cy="0.5" r="0.5">
            <stop stopColor="#1B2050" stopOpacity="0.18" />
            <stop offset="1" stopColor="#1B2050" stopOpacity="0" />
          </radialGradient>
        </defs>

        <ellipse cx="182" cy="268" rx="104" ry="22" fill="url(#bot-shadow)" />

        <path
          d="M112 196c-18 4-30 16-33 31"
          stroke="#CDD1E2"
          strokeWidth="15"
          strokeLinecap="round"
        />
        <path
          d="M248 196c18 4 30 16 33 31"
          stroke="#CDD1E2"
          strokeWidth="15"
          strokeLinecap="round"
        />
        <circle cx="79" cy="229" r="10" fill="#F7F8FC" stroke="#CDD1E2" strokeWidth="4" />
        <circle cx="282" cy="229" r="10" fill="#F7F8FC" stroke="#CDD1E2" strokeWidth="4" />

        <rect x="120" y="146" width="120" height="94" rx="38" fill="url(#bot-shell)" />
        <rect x="146" y="176" width="68" height="34" rx="16" fill="#FFFFFF" stroke="#D8DBE9" />
        <circle cx="164" cy="193" r="4.5" fill="#62C9EE" />
        <circle cx="180" cy="193" r="4.5" fill="#B8BFD6" />
        <circle cx="196" cy="193" r="4.5" fill="#B8BFD6" />

        <path d="M180 63V48" stroke="#C7CBDD" strokeWidth="6" strokeLinecap="round" />
        <circle cx="180" cy="43" r="8" fill="#22242F" />
        <circle cx="180" cy="43" r="3" fill="#8FE7FF" />

        <rect x="112" y="75" width="136" height="112" rx="42" fill="url(#bot-shell)" />
        <rect x="127" y="91" width="106" height="80" rx="31" fill="url(#bot-face)" />
        <circle cx="160" cy="129" r="9" fill="url(#bot-glow)" />
        <circle cx="200" cy="129" r="9" fill="url(#bot-glow)" />
        <circle cx="163" cy="126" r="3" fill="#FFFFFF" opacity="0.85" />
        <circle cx="203" cy="126" r="3" fill="#FFFFFF" opacity="0.85" />
        <path
          d="M168 151c4 5 9 7 14 7s10-2 14-7"
          stroke="#6FD8F7"
          strokeWidth="4"
          strokeLinecap="round"
        />

        <rect x="96" y="116" width="15" height="34" rx="7.5" fill="#E1E4F0" />
        <rect x="249" y="116" width="15" height="34" rx="7.5" fill="#E1E4F0" />
      </svg>
    </div>
  );
}
