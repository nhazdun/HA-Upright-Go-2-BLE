/**
 * Upright GO 2 posture card.
 *
 * A live posture read-out modelled on the official app: a progress ring that
 * turns red when you slouch, a silhouette that leans with the measured angle,
 * and the calibrate / vibration / delay row underneath.
 */

const GREEN = "#20c05c";
const GREEN_SOFT = "#8fe0ac";
const RED = "#e2483c";
const RED_SOFT = "#f0938c";

const DEFAULTS = {
  // Angle readings that correspond to fully upright and fully slouched. The
  // device reports a raw sensor angle whose usable range depends on how the
  // unit sits on the back, so both ends are configurable.
  upright_angle: 35,
  slouch_angle: 75,
  max_tilt: 32,
};

class UprightGo2Card extends HTMLElement {
  static getConfigElement() {
    return document.createElement("upright-go2-card-editor");
  }

  static getStubConfig(hass) {
    const find = (domain, suffix) =>
      Object.keys(hass.states).find(
        (id) => id.startsWith(`${domain}.`) && id.includes(suffix),
      );
    return {
      type: "custom:upright-go2-card",
      angle: find("sensor", "posture_angle"),
      slouching: find("binary_sensor", "slouching"),
      battery: find("sensor", "upright_go_2_battery"),
      slouching_time: find("sensor", "slouching_time"),
      upright_time: find("sensor", "upright_time"),
      vibration: find("switch", "vibration"),
      delay: find("number", "vibration_delay"),
      calibrate: find("button", "calibrate"),
    };
  }

  setConfig(config) {
    if (!config.angle && !config.slouching) {
      throw new Error("Set at least `angle` or `slouching`");
    }
    this._config = { ...DEFAULTS, ...config };
    if (!this.shadowRoot) this.attachShadow({ mode: "open" });
    this._built = false;
  }

  getCardSize() {
    return 8;
  }

  getGridOptions() {
    return { columns: 12, rows: 8, min_columns: 6 };
  }

  /** The entity keys this card reads, in a stable order. */
  static get WATCHED() {
    return [
      "angle", "slouching", "battery", "upright_time",
      "slouching_time", "vibration", "delay",
    ];
  }

  set hass(hass) {
    this._hass = hass;
    if (!this._built) this._build();

    // Home Assistant sets `hass` on every state change anywhere in the
    // instance, which on a busy system is many times a second. Re-rendering
    // each time thrashed the DOM and made the figure stutter, so bail out
    // unless something this card actually shows has moved.
    const signature = UprightGo2Card.WATCHED.map((key) => {
      const id = this._config[key];
      const s = id ? hass.states[id] : undefined;
      return s ? s.state : "";
    }).join("|");
    if (signature === this._signature) return;
    this._signature = signature;

    this._render();
  }

  _state(key) {
    const id = this._config[key];
    if (!id) return undefined;
    return this._hass.states[id];
  }

  _num(key) {
    const s = this._state(key);
    if (!s) return undefined;
    const v = Number(s.state);
    return Number.isFinite(v) ? v : undefined;
  }

  _build() {
    this.shadowRoot.innerHTML = `
      <style>
        ha-card {
          padding: 16px;
          display: flex;
          flex-direction: column;
          gap: 12px;
          height: 100%;
          box-sizing: border-box;
        }
        .top {
          display: flex;
          align-items: center;
          justify-content: space-between;
          gap: 12px;
        }
        .totals { display: flex; gap: 16px; align-items: baseline; }
        .total { display: flex; flex-direction: column; gap: 2px; }
        .total b { font-size: 1.35rem; line-height: 1; font-weight: 600; }
        .total span {
          font-size: 0.72rem;
          text-transform: uppercase;
          letter-spacing: 0.04em;
          color: var(--secondary-text-color);
        }
        .up b { color: ${GREEN}; }
        .down b { color: ${RED}; }
        .pill {
          display: inline-flex;
          align-items: center;
          gap: 6px;
          padding: 5px 12px;
          border-radius: 999px;
          font-size: 0.8rem;
          font-weight: 600;
          white-space: nowrap;
        }
        .dot { width: 8px; height: 8px; border-radius: 50%; }
        .stage { flex: 1; display: flex; justify-content: center; min-height: 0; }
        svg { width: 100%; height: 100%; max-height: 320px; overflow: visible; }
        .ring-track { stroke: var(--divider-color, #e3e3e3); }
        .ring {
          transition: stroke-dashoffset .5s linear, stroke .2s ease;
          stroke-linecap: round;
        }
        #figure {
          transition: transform .5s linear;
          transform-origin: 100px 168px;
          will-change: transform;
        }
        #figure path, #figure circle { transition: fill .25s ease; }
        @media (prefers-reduced-motion: reduce) {
          .ring, #figure, #figure path, #figure circle { transition: none; }
        }
        .angle {
          font-size: 13px;
          fill: var(--secondary-text-color);
          text-anchor: middle;
          font-family: inherit;
        }
        .row {
          display: grid;
          grid-template-columns: repeat(3, 1fr);
          border-top: 1px solid var(--divider-color, #e3e3e3);
          padding-top: 10px;
          gap: 4px;
        }
        .cell {
          display: flex;
          flex-direction: column;
          align-items: center;
          gap: 4px;
          padding: 4px 2px;
          border-radius: 10px;
          cursor: pointer;
          -webkit-tap-highlight-color: transparent;
        }
        .cell:hover { background: var(--secondary-background-color); }
        .cell[disabled] { cursor: default; opacity: .5; }
        .cell small {
          font-size: 0.74rem;
          color: var(--secondary-text-color);
          text-align: center;
        }
        .cell ha-icon { --mdc-icon-size: 22px; }
        .cell .val { font-size: 0.82rem; font-weight: 600; }
      </style>
      <ha-card>
        <div class="top">
          <div class="totals">
            <div class="total up"><b id="t-up">–</b><span id="l-up">upright</span></div>
            <div class="total down"><b id="t-down">–</b><span id="l-down">slouching</span></div>
          </div>
          <div class="pill" id="pill"><span class="dot" id="dot"></span><span id="pill-text"></span></div>
        </div>

        <div class="stage">
          <svg viewBox="0 0 200 210" preserveAspectRatio="xMidYMid meet" role="img">
            <circle class="ring-track" cx="100" cy="100" r="82" fill="none" stroke-width="11"/>
            <circle class="ring" id="ring" cx="100" cy="100" r="82" fill="none" stroke-width="11"
                    transform="rotate(-90 100 100)"/>
            <g id="figure">
              <circle id="head" cx="100" cy="62" r="21"/>
              <path id="body" d="M100 92c-19 0-31 13-33 31-2 17-3 30-3 41h72c0-11-1-24-3-41-2-18-14-31-33-31z"/>
              <path id="belly" opacity=".45"
                    d="M118 96c9 6 13 16 14 27 1 12 1 25 1 41h-24c0-16 2-28 4-38 2-11 3-22 5-30z"/>
            </g>
            <text class="angle" id="angle" x="100" y="203"></text>
          </svg>
        </div>

        <div class="row">
          <div class="cell" id="c-cal"><ha-icon icon="mdi:crosshairs-gps"></ha-icon><small>Calibrate</small></div>
          <div class="cell" id="c-vib"><ha-icon id="i-vib" icon="mdi:vibrate"></ha-icon><small id="s-vib">Vibration</small></div>
          <div class="cell" id="c-del"><ha-icon icon="mdi:timer-outline"></ha-icon><small><span class="val" id="s-del">–</span></small></div>
        </div>
      </ha-card>
    `;

    const $ = (id) => this.shadowRoot.getElementById(id);
    this._el = {
      ring: $("ring"), figure: $("figure"), head: $("head"), body: $("body"),
      belly: $("belly"), angle: $("angle"), pill: $("pill"), dot: $("dot"),
      pillText: $("pill-text"), tUp: $("t-up"), tDown: $("t-down"),
      cCal: $("c-cal"), cVib: $("c-vib"), cDel: $("c-del"),
      iVib: $("i-vib"), sVib: $("s-vib"), sDel: $("s-del"),
    };

    const circumference = 2 * Math.PI * 82;
    this._el.ring.setAttribute("stroke-dasharray", `${circumference}`);
    this._circumference = circumference;

    this._el.cCal.addEventListener("click", () => {
      if (!this._config.calibrate) return;
      this._hass.callService("button", "press", {
        entity_id: this._config.calibrate,
      });
    });
    this._el.cVib.addEventListener("click", () => {
      if (!this._config.vibration) return;
      this._hass.callService("switch", "toggle", {
        entity_id: this._config.vibration,
      });
    });
    this._el.cDel.addEventListener("click", () => {
      if (!this._config.delay) return;
      this._more(this._config.delay);
    });

    this._built = true;
  }

  _more(entityId) {
    const event = new Event("hass-more-info", { bubbles: true, composed: true });
    event.detail = { entityId };
    this.dispatchEvent(event);
  }

  /** Format a duration sensor, whatever unit it is displayed in, as h/min. */
  _duration(key) {
    const s = this._state(key);
    if (!s || Number.isNaN(Number(s.state))) return "–";
    const unit = s.attributes.unit_of_measurement || "s";
    const per = { s: 1, min: 60, h: 3600, d: 86400 }[unit] ?? 1;
    const seconds = Number(s.state) * per;
    if (seconds < 60) return `${Math.round(seconds)}s`;
    const minutes = Math.round(seconds / 60);
    if (minutes < 60) return `${minutes}m`;
    return `${Math.floor(minutes / 60)}h ${minutes % 60}m`;
  }

  _set(node, prop, value) {
    // Writing an unchanged style still costs a style recalculation.
    if (this._last === undefined) this._last = new Map();
    const key = `${node.id}.${prop}`;
    if (this._last.get(key) === value) return;
    this._last.set(key, value);
    if (prop === "text") node.textContent = value;
    else node.style[prop] = value;
  }

  _render() {
    const el = this._el;
    const cfg = this._config;

    const slouchState = this._state("slouching");
    const slouching = slouchState ? slouchState.state === "on" : false;
    const known = slouchState && !["unknown", "unavailable"].includes(slouchState.state);

    const angle = this._num("angle");
    const span = cfg.slouch_angle - cfg.upright_angle;
    let ratio = 0;
    if (angle !== undefined && span !== 0) {
      ratio = Math.min(1, Math.max(0, (angle - cfg.upright_angle) / span));
    }

    // The ring fills as posture degrades, matching the app's behaviour.
    const filled = known ? 0.15 + ratio * 0.85 : 0;
    this._set(el.ring, "strokeDashoffset", `${this._circumference * (1 - filled)}`);
    this._set(el.ring, "stroke", slouching ? RED : GREEN);

    this._set(el.figure, "transform", `rotate(${(ratio * cfg.max_tilt).toFixed(1)}deg)`);
    const solid = slouching ? RED : GREEN;
    const soft = slouching ? RED_SOFT : GREEN_SOFT;
    this._set(el.head, "fill", soft);
    this._set(el.body, "fill", solid);
    this._set(el.belly, "fill", soft);

    this._set(el.angle, "text", angle === undefined ? "" : `${angle.toFixed(1)}°`);

    const battery = this._num("battery");
    let label;
    if (!known) label = "No data";
    else label = slouching ? "Slouching" : "Upright";
    if (battery !== undefined) label += ` · ${Math.round(battery)}%`;
    el.pillText.textContent = label;
    el.dot.style.background = known ? (slouching ? RED : GREEN) : "var(--disabled-text-color)";
    el.pill.style.background = known
      ? slouching ? "rgba(226,72,60,.12)" : "rgba(32,192,92,.12)"
      : "var(--secondary-background-color)";
    el.pill.style.color = known ? (slouching ? RED : GREEN) : "var(--secondary-text-color)";

    this._set(el.tUp, "text", this._duration("upright_time"));
    this._set(el.tDown, "text", this._duration("slouching_time"));

    const vib = this._state("vibration");
    const vibOn = vib ? vib.state === "on" : undefined;
    el.iVib.setAttribute("icon", vibOn === false ? "mdi:vibrate-off" : "mdi:vibrate");
    el.iVib.style.color = vibOn ? GREEN : "var(--secondary-text-color)";
    el.sVib.textContent = vibOn === undefined ? "Vibration" : vibOn ? "Vibration on" : "Vibration off";
    el.cVib.toggleAttribute("disabled", !this._config.vibration);

    const delay = this._num("delay");
    el.sDel.textContent = delay === undefined ? "Delay" : `${Math.round(delay)}s delay`;
    el.cDel.toggleAttribute("disabled", !this._config.delay);
    el.cCal.toggleAttribute("disabled", !this._config.calibrate);
  }
}

customElements.define("upright-go2-card", UprightGo2Card);

window.customCards = window.customCards || [];
window.customCards.push({
  type: "upright-go2-card",
  name: "Upright GO 2 posture",
  description: "Live posture figure, ring and controls for the Upright GO 2",
  preview: true,
  documentationURL: "https://github.com/nhazdun/HA-Upright-Go-2-BLE",
});

console.info("%c UPRIGHT-GO-2-CARD ", "background:#20c05c;color:#fff;border-radius:3px");
