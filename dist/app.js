const state = {
  data: null,
  scope: "all",
  selectedThemeCode: null,
  sectorQuery: "",
  sectorKind: "all",
  sectorSort: "ret15",
  sectorLimit: 80,
};

const dom = {
  asof: document.getElementById("asof-pill"),
  source: document.getElementById("source-pill"),
  coverage: document.getElementById("coverage-pill"),
  marketSignal: document.getElementById("market-signal"),
  limitRatio: document.getElementById("limit-ratio"),
  limitContext: document.getElementById("limit-context"),
  breakRate: document.getElementById("break-rate"),
  streakContext: document.getElementById("streak-context"),
  aliveCount: document.getElementById("alive-count"),
  marketPhase: document.getElementById("market-phase"),
  narrativeGrid: document.getElementById("narrative-grid"),
  themeGrid: document.getElementById("theme-grid"),
  timelineGrid: document.getElementById("timeline-grid"),
  timelineTitle: document.getElementById("timeline-title"),
  timelineNote: document.getElementById("timeline-note"),
  cycleStage: document.getElementById("cycle-stage"),
  cycleSummary: document.getElementById("cycle-summary"),
  cycleStats: document.getElementById("cycle-stats"),
  cycleTrigger: document.getElementById("cycle-trigger"),
  cycleInvalidation: document.getElementById("cycle-invalidation"),
  candidateGrid: document.getElementById("candidate-grid"),
  sectorSearch: document.getElementById("sector-search"),
  sectorKind: document.getElementById("sector-kind"),
  sectorSort: document.getElementById("sector-sort"),
  sectorBody: document.getElementById("sector-body"),
  sectorCount: document.getElementById("sector-count"),
  loadMore: document.getElementById("load-more"),
  universeSummary: document.getElementById("universe-summary"),
  refreshButton: document.getElementById("refresh-button"),
  footerSource: document.getElementById("footer-source"),
};

const number = (value, digits = 2) =>
  value === null || value === undefined || Number.isNaN(Number(value))
    ? "—"
    : Number(value).toFixed(digits);

const pct = (value, digits = 2) => {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return "—";
  const numeric = Number(value);
  return `${numeric > 0 ? "+" : ""}${numeric.toFixed(digits)}%`;
};

const ratio = (value) =>
  value === null || value === undefined || Number.isNaN(Number(value))
    ? "—"
    : `${Number(value).toFixed(2)}×`;

const directionClass = (value) => {
  const numeric = Number(value);
  if (numeric > 0) return "up";
  if (numeric < 0) return "down";
  return "";
};

const escapeHtml = (value) =>
  String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");

const formatDate = (compact) => {
  if (!compact || compact.length !== 8) return compact || "—";
  return `${compact.slice(0, 4)}.${compact.slice(4, 6)}.${compact.slice(6, 8)}`;
};

function renderHeader(data) {
  const { meta, market } = data;
  document.title = `${meta.title} · ${formatDate(meta.asof)}`;
  dom.asof.textContent = `数据截至 ${formatDate(meta.asof)}`;
  dom.source.textContent = meta.source;
  dom.coverage.textContent = `${meta.sectorCount} 个板块 · 行业 + 概念`;
  dom.marketSignal.innerHTML = `
    <span class="signal-orbit" aria-hidden="true"></span>
    <div><span>当前情绪</span><strong>${escapeHtml(market.phase)}</strong></div>
  `;
  dom.limitRatio.textContent = `${market.limitUp} / ${market.limitDown}`;
  dom.limitContext.textContent = `近5日涨停均值 ${number(market.avg5LimitUp, 1)}`;
  dom.breakRate.textContent = `${number(market.breakRate, 1)}%`;
  dom.streakContext.textContent = `最高 ${market.maxStreak} 板 · 连板 ${market.streakCount} 只`;
  dom.aliveCount.textContent = `${market.aliveMainlines} 个`;
  dom.marketPhase.textContent = market.phase;
  dom.universeSummary.textContent =
    `回顾 ${meta.window.start}—${meta.window.end}，确认窗口最近 ${meta.recentWindow.days} 个交易日。`;
  dom.footerSource.textContent = `${meta.source} · 最近生成 ${new Date(meta.generatedAt).toLocaleString("zh-CN")}`;
}

function metricHtml(label, value) {
  return `<div class="theme-metric"><span>${label}</span><strong>${value}</strong></div>`;
}

function renderNarratives() {
  const narratives = state.data.narratives || [];
  if (!narratives.length) {
    dom.narrativeGrid.innerHTML =
      '<div class="loading-panel">今日叙事数据尚未生成，请稍后刷新。</div>';
    return;
  }

  dom.narrativeGrid.innerHTML = narratives
    .map(
      (theme) => `
      <article class="narrative-card" data-tone="${theme.tone}">
        <div class="narrative-head">
          <div class="narrative-title">
            <strong>${escapeHtml(theme.name)}</strong>
            <span>${escapeHtml(theme.stage)}</span>
          </div>
          ${
            theme.source
              ? `<a class="narrative-source" href="${escapeHtml(
                  theme.source,
                )}" target="_blank" rel="noreferrer">消息源 ↗</a>`
              : ""
          }
        </div>
        <p class="narrative-evidence">${escapeHtml(theme.evidence)}</p>
        <div class="narrative-catalyst">
          <strong>催化 / 资金逻辑</strong>
          ${escapeHtml(theme.catalyst)}
        </div>
        <div class="narrative-stocks">
          ${theme.stocks
            .map(
              (stock) => `
              <div class="narrative-stock">
                <span class="narrative-role">${escapeHtml(stock.role)}</span>
                <span class="narrative-stock-name">
                  <strong>${escapeHtml(stock.name)}</strong>
                  <small>${escapeHtml(stock.code)}</small>
                </span>
                <span class="narrative-change">
                  <strong class="${directionClass(stock.pct)}">${pct(stock.pct)}</strong>
                  <small>${escapeHtml(stock.change)}</small>
                </span>
              </div>
            `,
            )
            .join("")}
        </div>
      </article>
    `,
    )
    .join("");
}

function stockRow(stock) {
  return `
    <div class="stock-row">
      <span class="stock-role">${escapeHtml(stock.role)}</span>
      <span class="stock-name">
        <strong>${escapeHtml(stock.name)}</strong>
        <small>${escapeHtml(stock.ts_code || "")} · ${stock.above_ma10 ? "10日线上" : "10日线下"}</small>
      </span>
      <span class="stock-change">
        <strong class="${directionClass(stock.ret_recent)}">${pct(stock.ret_recent)}</strong>
        <small>15日 ${pct(stock.ret_lookback)}</small>
      </span>
    </div>
  `;
}

function renderThemes() {
  const themes = state.data.themes.filter(
    (theme) => state.scope === "all" || theme.scope === state.scope,
  );
  if (!themes.length) {
    dom.themeGrid.innerHTML = '<div class="loading-panel">当前范围没有题材。</div>';
    return;
  }
  dom.themeGrid.innerHTML = themes
    .map(
      (theme) => `
      <article class="theme-card ${state.selectedThemeCode === theme.code ? "is-selected" : ""}"
        data-stage="${escapeHtml(theme.stageKey)}" data-theme-code="${escapeHtml(theme.code)}"
        role="button" tabindex="0" aria-pressed="${state.selectedThemeCode === theme.code}"
        aria-label="查看${escapeHtml(theme.name)}的完整周期">
        <div class="theme-head">
          <div class="theme-name">
            <strong>${escapeHtml(theme.name)}</strong>
            <span>${theme.scope === "mainline" ? "当前主线" : "潜在方向"} · ${
              theme.kind === "industry" ? "行业" : "概念"
            }</span>
          </div>
          <span class="stage-badge">${escapeHtml(theme.stage)}</span>
        </div>
        <div class="theme-metrics">
          ${metricHtml("15日", pct(theme.metrics.ret15))}
          ${metricHtml("5日", pct(theme.metrics.ret5))}
          ${metricHtml("上榜", `${theme.metrics.rankDays}天`)}
          ${metricHtml("量比", number(theme.metrics.volRatio))}
        </div>
        <p class="theme-note">${escapeHtml(theme.note)}</p>
        <div class="stock-ladder">
          ${theme.stocks.map(stockRow).join("")}
        </div>
        <span class="theme-cycle-link">${state.selectedThemeCode === theme.code ? "正在查看周期" : "查看完整周期"}<span aria-hidden="true">→</span></span>
      </article>
    `,
    )
    .join("");
}

function selectTheme(code, { scroll = true } = {}) {
  if (!state.data.themes.some((theme) => theme.code === code)) return;
  state.selectedThemeCode = code;
  renderThemes();
  renderTimeline();
  if (scroll) {
    document.getElementById("timeline").scrollIntoView({
      behavior: window.matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth",
      block: "start",
    });
  }
}

function timelineCellClass(value, code, name = "") {
  if (value === null || value === undefined) return "empty-cell";
  const isGrowth = code.startsWith("300") || code.startsWith("688");
  const isBse = code.startsWith("8") || code.startsWith("4") || code.startsWith("920");
  const limitUp = name.includes("ST") ? 4.7 : isBse ? 29.7 : isGrowth ? 19.7 : 9.7;
  if (value >= limitUp) return "limit-cell";
  if (value <= -limitUp) return "down-limit-cell";
  if (value >= 5) return "strong-cell";
  if (value > 0) return "up-cell";
  if (value <= -5) return "weak-cell";
  if (value < 0) return "down-cell";
  return "";
}

function renderTimeline() {
  const theme = state.data.themes.find((item) => item.code === state.selectedThemeCode) || state.data.themes[0];
  const timeline = theme?.cycle;
  if (!timeline?.stocks?.length) {
    dom.timelineGrid.innerHTML = '<div class="loading-panel">暂无周期矩阵数据。</div>';
    return;
  }

  dom.timelineTitle.textContent = `${theme.name} · 周期路径`;
  dom.timelineNote.textContent = `${timeline.dates.length} 个交易日 · 点击其他题材即可切换`;
  dom.timelineGrid.setAttribute("aria-label", `${theme.name}周期逐日矩阵`);
  dom.cycleStage.textContent = timeline.stage;
  dom.cycleStage.dataset.stage = timeline.stageKey;
  dom.cycleSummary.textContent = `${timeline.summary} ${theme.thesis || ""}`;
  dom.cycleTrigger.textContent = timeline.trigger;
  dom.cycleInvalidation.textContent = timeline.invalidation;
  const stats = timeline.stats || {};
  dom.cycleStats.innerHTML = `
    ${metricHtml("近5日", pct(stats.ret5))}
    ${metricHtml("同类分位", `${number(stats.rankPct, 0)}%`)}
    ${metricHtml("上涨家数", `${number(stats.breadth, 0)}%`)}
    ${metricHtml("大涨家数", `${stats.strongCount ?? 0}只`)}
  `;

  const dateCount = timeline.dates.length;
  dom.timelineGrid.style.gridTemplateColumns = `150px repeat(${dateCount}, minmax(44px, 1fr))`;
  dom.timelineGrid.style.minWidth = `${150 + dateCount * 46}px`;
  const parts = [];
  const add = (html, row, col, span = 1) =>
    parts.push(`<div style="grid-row:${row};grid-column:${col} / span ${span}">${html}</div>`);

  timeline.cycles.forEach((cycle, index) => {
    const row = index + 1;
    parts.push(
      `<div class="tl-band-row" style="grid-row:${row};grid-column:2 / span ${dateCount};z-index:0"></div>`,
    );
    add(
      `<div class="tl-label"><strong>${escapeHtml(cycle.label)}</strong><span>${escapeHtml(
        cycle.range,
      )}</span></div>`,
      row,
      1,
    );
    add(
      `<div class="tl-band tl-band-${escapeHtml(cycle.tone)}"><strong>${escapeHtml(cycle.title)}</strong><span>${escapeHtml(cycle.range)}</span></div>`,
      row,
      cycle.start + 1,
      cycle.span,
    );
  });

  const headerRow = timeline.cycles.length + 1;
  add('<div class="tl-label"><strong>板块 / 核心股</strong><span>周期角色</span></div>', headerRow, 1);
  timeline.dates.forEach((date, index) => {
    const stage = timeline.dailyStages?.[index] || "";
    add(`<div class="tl-date" title="${escapeHtml(`${date} ${stage}`)}">${escapeHtml(date)}</div>`, headerRow, index + 2);
  });

  timeline.stocks.forEach((stock, stockIndex) => {
    const row = headerRow + stockIndex + 1;
    add(
      `<div class="tl-label"><strong>${escapeHtml(stock.name)}</strong><span>${escapeHtml(
        stock.role,
      )}</span></div>`,
      row,
      1,
    );
    stock.pct.forEach((value, dateIndex) => {
      const event = stock.events?.[String(dateIndex)] || "";
      const cls = timelineCellClass(value, stock.code, stock.name);
      const valueText =
        value === null || value === undefined ? "—" : `${value > 0 ? "+" : ""}${number(value, 2)}`;
      add(
        `<div class="tl-cell ${cls}" title="${escapeHtml(
          `${timeline.dates[dateIndex]} ${stock.name} ${valueText}% ${event}`,
        )}">
          ${value === null || value === undefined ? "" : `<span>${valueText}</span>`}
          ${event ? `<span class="event-tag">${escapeHtml(event)}</span>` : ""}
        </div>`,
        row,
        dateIndex + 2,
      );
    });
  });

  dom.timelineGrid.innerHTML = parts.join("");
}

function renderCandidates() {
  const candidates = state.data.candidates || [];
  if (!candidates.length) {
    dom.candidateGrid.innerHTML =
      '<div class="loading-panel">当前没有同时满足位置、承接与量能条件的补涨候选。</div>';
    return;
  }
  dom.candidateGrid.innerHTML = candidates
    .map(
      (item) => `
      <article class="candidate-card">
        <div class="candidate-head">
          <div class="candidate-rank">
            <span class="candidate-number">${item.rank}</span>
            <div class="candidate-title">
              <strong>${escapeHtml(item.name)}</strong>
              <small>${escapeHtml(item.code)}</small>
            </div>
          </div>
          <span class="candidate-label">${escapeHtml(item.label)}</span>
        </div>
        <div class="candidate-metrics">
          <span class="candidate-metric"><span>今日</span> ${pct(item.today)}</span>
          <span class="candidate-metric"><span>5日</span> ${pct(item.ret5)}</span>
          <span class="candidate-metric"><span>3日额比</span> ${ratio(item.amountRatio3)}</span>
          <span class="candidate-metric"><span>主力</span> ${pct(item.mainFlowYi, 2).replace(
            "%",
            "亿",
          )}</span>
          <span class="candidate-metric"><span>市值</span> ${number(item.marketCapYi, 1)}亿</span>
        </div>
        <p class="candidate-evidence">
          15日 ${pct(item.ret15)}，距20日高点 ${pct(item.distanceHigh20)}。候选分仅表示当前各项条件的相对顺位。
        </p>
        <div class="candidate-rule">
          <div><span>启动</span><span>${escapeHtml(item.trigger)}</span></div>
          <div><span>失效</span><span>${escapeHtml(item.invalidation)}</span></div>
        </div>
      </article>
    `,
    )
    .join("");
}

function filteredSectors() {
  const query = state.sectorQuery.trim().toLowerCase();
  return state.data.sectors
    .filter((sector) => state.sectorKind === "all" || sector.kind === state.sectorKind)
    .filter(
      (sector) =>
        !query ||
        sector.name.toLowerCase().includes(query) ||
        sector.code.toLowerCase().includes(query),
    )
    .sort((a, b) => Number(b[state.sectorSort] ?? -Infinity) - Number(a[state.sectorSort] ?? -Infinity));
}

function renderSectors() {
  const rows = filteredSectors();
  const visible = rows.slice(0, state.sectorLimit);
  dom.sectorBody.innerHTML = visible
    .map(
      (sector) => `
      <tr>
        <td>
          <span class="sector-name">
            <i class="status-dot ${sector.status === "主线" ? "mainline" : sector.status === "潜在" ? "potential" : ""}"></i>
            ${escapeHtml(sector.name)}
          </span>
        </td>
        <td><span class="kind-tag">${sector.kind === "industry" ? "行业" : "概念"}</span></td>
        <td class="num ${directionClass(sector.ret15)}">${pct(sector.ret15)}</td>
        <td class="num ${directionClass(sector.ret5)}">${pct(sector.ret5)}</td>
        <td class="num">${sector.rankDays}</td>
        <td class="num">${number(sector.volRatio)}</td>
        <td class="num ${directionClass(sector.shareDelta)}">${pct(sector.shareDelta, 3)}</td>
      </tr>
    `,
    )
    .join("");
  dom.sectorCount.textContent = `显示 ${visible.length} / ${rows.length} 个板块`;
  dom.loadMore.hidden = visible.length >= rows.length;
}

async function loadData({ announce = false } = {}) {
  dom.refreshButton.classList.add("is-loading");
  dom.refreshButton.disabled = true;
  try {
    const response = await fetch(`./data/latest.json?v=${Date.now()}`, { cache: "no-store" });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    state.data = await response.json();
    if (!state.data.themes.some((theme) => theme.code === state.selectedThemeCode)) {
      state.selectedThemeCode = state.data.themes[0]?.code || null;
    }
    renderHeader(state.data);
    renderNarratives();
    renderThemes();
    renderTimeline();
    renderCandidates();
    renderSectors();
    if (announce) dom.refreshButton.lastChild.textContent = " 已刷新";
  } catch (error) {
    const message = `数据加载失败：${error.message}`;
    dom.narrativeGrid.innerHTML = `<div class="error-panel">${escapeHtml(message)}</div>`;
    dom.themeGrid.innerHTML = `<div class="error-panel">${escapeHtml(message)}</div>`;
    dom.candidateGrid.innerHTML = "";
    dom.timelineGrid.innerHTML = "";
    console.error(error);
  } finally {
    dom.refreshButton.classList.remove("is-loading");
    dom.refreshButton.disabled = false;
    if (announce) {
      window.setTimeout(() => {
        dom.refreshButton.lastChild.textContent = " 刷新数据";
      }, 1800);
    }
  }
}

document.querySelectorAll(".segment").forEach((button) => {
  button.addEventListener("click", () => {
    state.scope = button.dataset.scope;
    document.querySelectorAll(".segment").forEach((item) => {
      item.classList.toggle("is-active", item === button);
    });
    renderThemes();
  });
});

dom.themeGrid.addEventListener("click", (event) => {
  const card = event.target.closest("[data-theme-code]");
  if (card) selectTheme(card.dataset.themeCode);
});

dom.themeGrid.addEventListener("keydown", (event) => {
  if (event.key !== "Enter" && event.key !== " ") return;
  const card = event.target.closest("[data-theme-code]");
  if (!card) return;
  event.preventDefault();
  selectTheme(card.dataset.themeCode);
});

let sectorSearchTimer;
dom.sectorSearch.addEventListener("input", () => {
  window.clearTimeout(sectorSearchTimer);
  sectorSearchTimer = window.setTimeout(() => {
    state.sectorQuery = dom.sectorSearch.value;
    state.sectorLimit = 80;
    renderSectors();
  }, 120);
});

dom.sectorKind.addEventListener("change", () => {
  state.sectorKind = dom.sectorKind.value;
  state.sectorLimit = 80;
  renderSectors();
});

dom.sectorSort.addEventListener("change", () => {
  state.sectorSort = dom.sectorSort.value;
  state.sectorLimit = 80;
  renderSectors();
});

dom.loadMore.addEventListener("click", () => {
  state.sectorLimit += 80;
  renderSectors();
});

dom.refreshButton.addEventListener("click", () => loadData({ announce: true }));
loadData();
