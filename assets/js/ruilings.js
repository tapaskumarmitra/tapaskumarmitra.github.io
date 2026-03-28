'use strict';

(function initRuilingsPage() {
  const DATA_URL = '/assets/data/ruilings.json';
  const RELATED_DATA_URL = '/assets/data/ruilings_related_llm.json';

  const state = {
    entries: [],
    entriesById: new Map(),
    filtered: [],
    meta: null,
    relatedMap: {},
    page: 1,
    pageSize: 12,
    search: '',
    category: 'all',
    subCategory: 'all',
    stage: 'all',
    court: 'all',
    sort: 'serial-asc',
    llmSearch: {
      status: 'idle',
      query: '',
      rankedIds: [],
      requestSeq: 0,
      cache: new Map(),
    },
  };

  const els = {
    productTopbar: document.querySelector('.product-topbar'),
    searchInput: document.getElementById('searchInput'),
    categoryFilter: document.getElementById('categoryFilter'),
    subCategoryFilter: document.getElementById('subCategoryFilter'),
    stageFilter: document.getElementById('stageFilter'),
    courtFilter: document.getElementById('courtFilter'),
    sortFilter: document.getElementById('sortFilter'),
    resetFilters: document.getElementById('resetFilters'),
    resultCount: document.getElementById('resultCount'),
    activeFilters: document.getElementById('activeFilters'),
    cardsGrid: document.getElementById('cardsGrid'),
    paginationWrap: document.getElementById('paginationWrap'),
    statTotalEntries: document.getElementById('statTotalEntries'),
    statTotalCategories: document.getElementById('statTotalCategories'),
    statTotalSubCategories: document.getElementById('statTotalSubCategories'),
    statCourtsCovered: document.getElementById('statCourtsCovered'),
    newCaseReference: document.getElementById('newCaseReference'),
    newVerdict: document.getElementById('newVerdict'),
    newImpact: document.getElementById('newImpact'),
    optionalCategory: document.getElementById('optionalCategory'),
    optionalSubCategory: document.getElementById('optionalSubCategory'),
    optionalStage: document.getElementById('optionalStage'),
    optionalCourt: document.getElementById('optionalCourt'),
    optionalYear: document.getElementById('optionalYear'),
    optionalStatuteTags: document.getElementById('optionalStatuteTags'),
    optionalAdvocateNotes: document.getElementById('optionalAdvocateNotes'),
    optionalRelatedDetails: document.getElementById('optionalRelatedDetails'),
    submitAddRuiling: document.getElementById('submitAddRuiling'),
    addRuilingModal: document.getElementById('addRuilingModal'),
    atlasToastContainer: document.getElementById('atlasToastContainer'),
    viewRuilingModal: document.getElementById('viewRuilingModal'),
    viewRuilingModalLabel: document.getElementById('viewRuilingModalLabel'),
    viewRuilingMeta: document.getElementById('viewRuilingMeta'),
    viewRuilingVerdict: document.getElementById('viewRuilingVerdict'),
    viewRuilingImpact: document.getElementById('viewRuilingImpact'),
    viewRuilingTags: document.getElementById('viewRuilingTags'),
    viewRuilingNotes: document.getElementById('viewRuilingNotes'),
    viewRuilingRelatedDetails: document.getElementById('viewRuilingRelatedDetails'),
    viewRuilingSources: document.getElementById('viewRuilingSources'),
    viewRuilingRelatedHint: document.getElementById('viewRuilingRelatedHint'),
    viewRuilingRelatedList: document.getElementById('viewRuilingRelatedList'),
    copyViewCitation: document.getElementById('copyViewCitation'),
  };

  let viewModalInstance = null;
  let currentViewEntry = null;
  let addModalInstance = null;

  if (!els.cardsGrid) {
    return;
  }

  loadData();
  bindEvents();

  async function loadData() {
    try {
      const [response, relatedResponse] = await Promise.all([
        fetch(DATA_URL, { cache: 'no-store' }),
        fetch(RELATED_DATA_URL, { cache: 'no-store' }).catch(() => null),
      ]);

      if (!response.ok) {
        throw new Error('Failed to load data');
      }

      const payload = await response.json();
      state.meta = payload.meta || {};

      if (relatedResponse && relatedResponse.ok) {
        const relatedPayload = await relatedResponse.json();
        state.relatedMap = relatedPayload?.related || {};
      } else {
        state.relatedMap = {};
      }

      state.entries = (payload.entries || []).map((entry) => {
        const normalizedTags = dedupeTags(entry.statuteTags || []);
        const searchable = [
          entry.caseReference,
          entry.issue,
          entry.holding,
          entry.category,
          entry.subCategory,
          entry.court,
          entry.stage,
          normalizedTags.join(' '),
        ]
          .join(' ')
          .toLowerCase();

        return {
          ...entry,
          statuteTags: normalizedTags,
          searchable,
        };
      });

      state.entriesById = new Map(
        state.entries.map((entry) => [Number(entry.id || entry.serial), entry])
      );

      populateGlobalFilters();
      renderHeroStats();
      applyFiltersAndRender();
    } catch (error) {
      console.error(error);
      els.resultCount.textContent = 'Unable to load ruilings data.';
      els.cardsGrid.innerHTML = '<div class="ruiling-empty">Could not load data. Please try again later.</div>';
    }
  }

  function bindEvents() {
    initTopbarAutoHide();

    const debouncedSearch = debounce(() => {
      const rawQuery = els.searchInput.value || '';
      state.page = 1;
      state.search = normalizeSearchQuery(rawQuery);
      applyFiltersAndRender();
      triggerLlmSemanticSearch(rawQuery);
    }, 180);

    els.searchInput.addEventListener('input', debouncedSearch);

    els.categoryFilter.addEventListener('change', () => {
      state.category = els.categoryFilter.value;
      state.page = 1;
      updateSubCategoryFilter();
      state.subCategory = els.subCategoryFilter.value;
      applyFiltersAndRender();
    });

    els.subCategoryFilter.addEventListener('change', () => {
      state.subCategory = els.subCategoryFilter.value;
      state.page = 1;
      applyFiltersAndRender();
    });

    els.stageFilter.addEventListener('change', () => {
      state.stage = els.stageFilter.value;
      state.page = 1;
      applyFiltersAndRender();
    });

    els.courtFilter.addEventListener('change', () => {
      state.court = els.courtFilter.value;
      state.page = 1;
      applyFiltersAndRender();
    });

    els.sortFilter.addEventListener('change', () => {
      state.sort = els.sortFilter.value;
      state.page = 1;
      applyFiltersAndRender();
    });

    els.resetFilters.addEventListener('click', () => {
      resetFilters();
      applyFiltersAndRender();
    });

    els.paginationWrap.addEventListener('click', (event) => {
      const button = event.target.closest('button[data-page]');
      if (!button) {
        return;
      }

      const nextPage = Number(button.dataset.page);
      if (!Number.isFinite(nextPage) || nextPage < 1) {
        return;
      }

      state.page = nextPage;
      renderCards();
      renderPagination();
      renderResultCount();
      window.scrollTo({ top: document.getElementById('ruilings-catalogue').offsetTop - 86, behavior: 'smooth' });
    });

    els.cardsGrid.addEventListener('click', async (event) => {
      const viewButton = event.target.closest('button[data-view-id]');
      if (viewButton) {
        const entryId = Number(viewButton.dataset.viewId || 0);
        openRuilingModal(entryId);
        return;
      }

      const copyButton = event.target.closest('button[data-copy-ref]');
      if (copyButton) {
        const citation = copyButton.dataset.copyRef || '';
        if (!citation) {
          return;
        }

        const originalText = copyButton.textContent;
        const copied = await copyToClipboard(citation);
        copyButton.textContent = copied ? 'Copied' : 'Copy failed';
        setTimeout(() => {
          copyButton.textContent = originalText || 'Copy Citation';
        }, 1000);
        return;
      }

      const card = event.target.closest('.ruiling-card[data-entry-id]');
      if (!card) {
        return;
      }

      const interactiveTarget = event.target.closest('button, a, input, textarea, select, summary, [contenteditable="true"]');
      if (interactiveTarget && interactiveTarget !== card) {
        return;
      }

      const entryId = Number(card.dataset.entryId || 0);
      openRuilingModal(entryId);
    });

    els.cardsGrid.addEventListener('keydown', (event) => {
      if (event.key !== 'Enter' && event.key !== ' ') {
        return;
      }

      const card = event.target.closest('.ruiling-card[data-entry-id]');
      if (!card) {
        return;
      }

      const interactiveTarget = event.target.closest('button, a, input, textarea, select, summary, [contenteditable="true"]');
      if (interactiveTarget && interactiveTarget !== card) {
        return;
      }

      event.preventDefault();
      const entryId = Number(card.dataset.entryId || 0);
      openRuilingModal(entryId);
    });

    if (els.viewRuilingModal && window.bootstrap?.Modal) {
      viewModalInstance = new window.bootstrap.Modal(els.viewRuilingModal);

      els.viewRuilingModal.addEventListener('hidden.bs.modal', () => {
        currentViewEntry = null;
      });

      els.viewRuilingModal.addEventListener('click', async (event) => {
        const openButton = event.target.closest('button[data-open-entry-id]');
        if (openButton) {
          const entryId = Number(openButton.dataset.openEntryId || 0);
          openRuilingModal(entryId);
          return;
        }

        const copyButton = event.target.closest('button[data-copy-ref]');
        if (!copyButton) {
          return;
        }

        const citation = copyButton.dataset.copyRef || '';
        if (!citation) {
          return;
        }

        const previous = copyButton.textContent;
        const copied = await copyToClipboard(citation);
        copyButton.textContent = copied ? 'Copied' : 'Copy failed';
        setTimeout(() => {
          copyButton.textContent = previous || 'Copy Citation';
        }, 850);
      });
    }

    if (els.copyViewCitation) {
      els.copyViewCitation.addEventListener('click', async () => {
        const citation = currentViewEntry?.caseReference || '';
        if (!citation) {
          return;
        }

        const previous = els.copyViewCitation.textContent;
        const copied = await copyToClipboard(citation);
        els.copyViewCitation.textContent = copied ? 'Copied' : 'Copy failed';
        setTimeout(() => {
          els.copyViewCitation.textContent = previous || 'Copy Citation';
        }, 900);
      });
    }

    if (els.addRuilingModal && window.bootstrap?.Modal) {
      addModalInstance = new window.bootstrap.Modal(els.addRuilingModal);
    }

    if (els.submitAddRuiling) {
      els.submitAddRuiling.addEventListener('click', async () => {
        const caseReference = (els.newCaseReference?.value || '').trim();
        const verdict = (els.newVerdict?.value || '').trim();
        const impact = (els.newImpact?.value || '').trim();

        if (!caseReference || !verdict || !impact) {
          showToast('Required fields missing', 'Please fill Sl no./Case Reference, Verdict, and Impact.', 'warning');
          return;
        }

        const payload = buildAddPayload(caseReference, verdict, impact);
        const addBtn = els.submitAddRuiling;
        const previousLabel = addBtn.textContent;
        addBtn.disabled = true;
        addBtn.textContent = 'Adding...';
        showToast('Adding ruiling', 'Ruiling is being processed and added to Atlas.', 'info');

        try {
          const response = await fetch('/api/ruilings/add', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload),
          });

          const body = await response.json().catch(() => ({}));
          if (!response.ok || !body?.ok) {
            throw new Error('Could not add ruiling right now.');
          }

          if (body.entry) {
            upsertEntryInState(body.entry);
            if (body.meta) {
              state.meta = body.meta;
            }
            populateGlobalFilters();
            renderHeroStats();
            applyFiltersAndRender();
          }

          resetAddForm();
          if (addModalInstance) {
            addModalInstance.hide();
          }
          showToast('Ruiling Added', `Entry #${body.serial || ''} added successfully.`, 'success');
        } catch (error) {
          console.error(error);
          showToast('Add Failed', 'Could not add right now. Please try again.', 'danger');
        } finally {
          addBtn.disabled = false;
          addBtn.textContent = previousLabel || 'Add Ruiling';
        }
      });
    }
  }

  async function triggerLlmSemanticSearch(rawQuery) {
    const normalizedQuery = normalizeSearchQuery(rawQuery);

    if (!normalizedQuery || normalizedQuery.length < 3) {
      clearLlmSemanticSearch();
      return;
    }

    const cached = state.llmSearch.cache.get(normalizedQuery);
    if (Array.isArray(cached)) {
      state.llmSearch.status = 'ready';
      state.llmSearch.query = normalizedQuery;
      state.llmSearch.rankedIds = cached;
      applyFiltersAndRender();
      return;
    }

    const requestSeq = state.llmSearch.requestSeq + 1;
    state.llmSearch.requestSeq = requestSeq;
    state.llmSearch.status = 'loading';
    state.llmSearch.query = normalizedQuery;
    state.llmSearch.rankedIds = [];
    applyFiltersAndRender();

    try {
      const response = await fetch('/api/ruilings/search', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          query: oneLine(rawQuery),
          topK: 80,
        }),
      });

      const body = await response.json().catch(() => ({}));
      if (requestSeq !== state.llmSearch.requestSeq) {
        return;
      }

      if (!response.ok || !body?.ok) {
        throw new Error(body?.error || 'Semantic search unavailable');
      }

      const rankedIds = Array.isArray(body.rankedIds)
        ? [...new Set(
          body.rankedIds
            .map((item) => Number(item))
            .filter((id) => Number.isFinite(id) && id > 0)
        )]
        : [];

      state.llmSearch.status = 'ready';
      state.llmSearch.query = normalizedQuery;
      state.llmSearch.rankedIds = rankedIds;
      state.llmSearch.cache.set(normalizedQuery, rankedIds);
    } catch (error) {
      if (requestSeq !== state.llmSearch.requestSeq) {
        return;
      }
      console.warn('LLM semantic search unavailable, using keyword mode.', error);
      state.llmSearch.status = 'error';
      state.llmSearch.query = normalizedQuery;
      state.llmSearch.rankedIds = [];
    } finally {
      if (requestSeq === state.llmSearch.requestSeq) {
        applyFiltersAndRender();
      }
    }
  }

  function clearLlmSemanticSearch() {
    state.llmSearch.requestSeq += 1;
    state.llmSearch.status = 'idle';
    state.llmSearch.query = '';
    state.llmSearch.rankedIds = [];
  }

  function isSemanticRankingActive() {
    return Boolean(
      state.search
      && state.llmSearch.status === 'ready'
      && state.llmSearch.query === state.search
      && state.llmSearch.rankedIds.length
    );
  }

  function matchesScopeFilters(entry) {
    if (state.category !== 'all' && entry.category !== state.category) {
      return false;
    }

    if (state.subCategory !== 'all' && entry.subCategory !== state.subCategory) {
      return false;
    }

    if (state.stage !== 'all' && entry.stage !== state.stage) {
      return false;
    }

    if (state.court !== 'all' && entry.court !== state.court) {
      return false;
    }

    return true;
  }

  function matchesKeywordTerms(entry, terms) {
    if (!terms.length) {
      return true;
    }
    return terms.every((term) => entry.searchable.includes(term));
  }

  function initTopbarAutoHide() {
    if (!els.productTopbar) {
      return;
    }

    const topThreshold = 16;
    const minDelta = 10;
    let lastScrollY = window.scrollY || 0;
    let hidden = false;
    let ticking = false;

    const setHidden = (shouldHide) => {
      if (hidden === shouldHide) {
        return;
      }
      hidden = shouldHide;
      els.productTopbar.classList.toggle('topbar-hidden', shouldHide);
      document.body.classList.toggle('topbar-collapsed', shouldHide);
    };

    const update = () => {
      const currentScrollY = window.scrollY || 0;

      if (currentScrollY <= topThreshold) {
        setHidden(false);
        lastScrollY = currentScrollY;
        return;
      }

      const delta = currentScrollY - lastScrollY;
      if (Math.abs(delta) < minDelta) {
        return;
      }

      if (delta > 0) {
        setHidden(true);
      } else {
        setHidden(false);
      }

      lastScrollY = currentScrollY;
    };

    window.addEventListener(
      'scroll',
      () => {
        if (ticking) {
          return;
        }
        ticking = true;
        window.requestAnimationFrame(() => {
          update();
          ticking = false;
        });
      },
      { passive: true }
    );
  }

  function resetFilters() {
    state.search = '';
    state.category = 'all';
    state.subCategory = 'all';
    state.stage = 'all';
    state.court = 'all';
    state.sort = 'serial-asc';
    state.page = 1;
    clearLlmSemanticSearch();

    els.searchInput.value = '';
    els.categoryFilter.value = 'all';
    updateSubCategoryFilter();
    els.subCategoryFilter.value = 'all';
    els.stageFilter.value = 'all';
    els.courtFilter.value = 'all';
    els.sortFilter.value = 'serial-asc';
  }

  function applyFiltersAndRender() {
    const terms = state.search ? state.search.split(/\s+/).filter(Boolean) : [];
    const scopedEntries = state.entries.filter((entry) => matchesScopeFilters(entry));
    const keywordFiltered = scopedEntries.filter((entry) => matchesKeywordTerms(entry, terms));
    const semanticActive = isSemanticRankingActive();

    let filtered = keywordFiltered;
    if (semanticActive) {
      const scopedById = new Map(
        scopedEntries.map((entry) => [Number(entry.id || entry.serial), entry])
      );

      const rankedEntries = [];
      const rankedSet = new Set();
      state.llmSearch.rankedIds.forEach((id) => {
        const entry = scopedById.get(Number(id));
        if (!entry) {
          return;
        }

        const entryId = Number(entry.id || entry.serial);
        if (rankedSet.has(entryId)) {
          return;
        }

        rankedSet.add(entryId);
        rankedEntries.push(entry);
      });

      const keywordExtras = keywordFiltered.filter(
        (entry) => !rankedSet.has(Number(entry.id || entry.serial))
      );
      filtered = [...rankedEntries, ...keywordExtras];
    }

    const preserveSemanticOrder = semanticActive && state.sort === 'serial-asc';
    state.filtered = preserveSemanticOrder ? filtered : sortEntries(filtered, state.sort);

    const totalPages = Math.max(1, Math.ceil(state.filtered.length / state.pageSize));
    if (state.page > totalPages) {
      state.page = totalPages;
    }

    renderCards();
    renderPagination();
    renderResultCount();
    renderActiveFilters();
  }

  function populateGlobalFilters() {
    fillSelect(els.categoryFilter, uniqueSorted(state.entries.map((entry) => entry.category)), 'All categories');
    fillSelect(els.stageFilter, uniqueSorted(state.entries.map((entry) => entry.stage)), 'All stages');
    fillSelect(els.courtFilter, uniqueSorted(state.entries.map((entry) => entry.court)), 'All courts');
    updateSubCategoryFilter();
  }

  function updateSubCategoryFilter() {
    const scopedEntries = state.category === 'all'
      ? state.entries
      : state.entries.filter((entry) => entry.category === state.category);

    const selectedBefore = els.subCategoryFilter.value;
    fillSelect(els.subCategoryFilter, uniqueSorted(scopedEntries.map((entry) => entry.subCategory)), 'All sub-categories');

    if ([...els.subCategoryFilter.options].some((option) => option.value === selectedBefore)) {
      els.subCategoryFilter.value = selectedBefore;
    } else {
      els.subCategoryFilter.value = 'all';
    }
  }

  function renderHeroStats() {
    const totalEntries = state.meta?.totalEntries || state.entries.length;
    const totalCategories = Object.keys(state.meta?.categoryCounts || {}).length;
    const totalSubCategories = (state.meta?.subCategoryBreakdown || []).length;
    const courtsCovered = uniqueSorted(state.entries.map((entry) => entry.court)).length;

    els.statTotalEntries.textContent = String(totalEntries);
    els.statTotalCategories.textContent = String(totalCategories);
    els.statTotalSubCategories.textContent = String(totalSubCategories);
    els.statCourtsCovered.textContent = String(courtsCovered);
  }

  function renderCards() {
    if (!state.filtered.length) {
      els.cardsGrid.innerHTML = '<div class="ruiling-empty">No matching ruilings found. Try broadening keywords or resetting filters.</div>';
      return;
    }

    const startIndex = (state.page - 1) * state.pageSize;
    const endIndex = startIndex + state.pageSize;
    const currentSlice = state.filtered.slice(startIndex, endIndex);

    els.cardsGrid.innerHTML = currentSlice
      .map((entry) => {
        const serialDisplay = String(entry.serial).padStart(3, '0');
        const quickTake = buildQuickTake(entry);
        const issuePreview = buildPreviewText(entry.issue || '', 170);
        const holdingPreview = buildPreviewText(entry.holding || '', 190);
        const tags = dedupeTags(entry.statuteTags || []).slice(0, 3);
        const taxonomy = `${entry.category || 'General'} \u203a ${entry.subCategory || 'General'}`;
        const primaryNote = safeTextList(entry.advocateNotes || [], 1)[0]
          || 'Match jurisdiction + facts before relying on this authority.';

        const sectionAnchors = tags.length
          ? tags
            .map((tag) => `<span class="ruiling-inline-tag">${escapeHtml(tag)}</span>`)
            .join('')
          : '<span class="ruiling-inline-tag is-muted">No statutory anchor captured</span>';

        return `
          <article
            class="ruiling-card"
            data-entry-id="${Number(entry.id || entry.serial)}"
            role="button"
            tabindex="0"
            aria-label="Open ruiling #${serialDisplay} details"
          >
            <div class="ruiling-card-top">
              <span class="ruiling-serial">#${serialDisplay}</span>
              <span class="ruiling-stage">${escapeHtml(entry.stage || 'General')}</span>
            </div>

            <p class="ruiling-reference">${escapeHtml(entry.caseReference || '')}</p>

            <div class="ruiling-meta">
              <span class="ruiling-meta-item"><i class="bi bi-building me-1" aria-hidden="true"></i>${escapeHtml(entry.court || 'Reported Court')}</span>
              <span class="ruiling-meta-sep">&#8226;</span>
              <span class="ruiling-meta-item"><i class="bi bi-calendar3 me-1" aria-hidden="true"></i>${entry.year ? escapeHtml(String(entry.year)) : 'Year not captured'}</span>
            </div>

            <p class="ruiling-quicktake">${escapeHtml(quickTake)}</p>

            <p class="ruiling-taxonomy">${escapeHtml(taxonomy)}</p>
            <p class="ruiling-detail-line"><span class="ruiling-detail-label">Issue:</span> ${escapeHtml(issuePreview)}</p>
            <p class="ruiling-detail-line"><span class="ruiling-detail-label">Holding:</span> ${escapeHtml(holdingPreview)}</p>
            <p class="ruiling-practice-note"><span class="ruiling-detail-label">Practice Note:</span> ${escapeHtml(primaryNote)}</p>
            <div class="ruiling-inline-tags" aria-label="Statutory anchors">
              ${sectionAnchors}
            </div>

            <div class="ruiling-card-footer">
              <button type="button" class="mini-action-btn" data-copy-ref="${escapeAttribute(entry.caseReference || '')}">Copy Citation</button>
            </div>
          </article>
        `;
      })
      .join('');
  }

  function renderPagination() {
    const totalItems = state.filtered.length;
    const totalPages = Math.max(1, Math.ceil(totalItems / state.pageSize));

    if (totalItems <= state.pageSize) {
      els.paginationWrap.innerHTML = '';
      return;
    }

    const pages = [];
    const start = Math.max(1, state.page - 2);
    const end = Math.min(totalPages, state.page + 2);
    for (let page = start; page <= end; page += 1) {
      pages.push(page);
    }

    els.paginationWrap.innerHTML = `
      <div class="ruiling-pagination">
        <button type="button" class="ruiling-page-btn" data-page="${state.page - 1}" ${state.page === 1 ? 'disabled' : ''} aria-label="Previous page">
          <i class="bi bi-chevron-left" aria-hidden="true"></i>
        </button>
        ${pages
          .map((page) => `
            <button type="button" class="ruiling-page-btn ${page === state.page ? 'active' : ''}" data-page="${page}" aria-label="Page ${page}">
              ${page}
            </button>
          `)
          .join('')}
        <button type="button" class="ruiling-page-btn" data-page="${state.page + 1}" ${state.page === totalPages ? 'disabled' : ''} aria-label="Next page">
          <i class="bi bi-chevron-right" aria-hidden="true"></i>
        </button>
      </div>
    `;
  }

  function renderResultCount() {
    const total = state.filtered.length;
    const semanticActive = isSemanticRankingActive();
    const semanticLoading = Boolean(
      state.search
      && state.llmSearch.status === 'loading'
      && state.llmSearch.query === state.search
    );
    const semanticLabel = semanticLoading
      ? ' • Refining with AI search...'
      : (semanticActive ? ' • AI semantic ranking active' : '');

    if (!total) {
      els.resultCount.textContent = `Showing 0 results.${semanticLabel}`;
      return;
    }

    const start = (state.page - 1) * state.pageSize + 1;
    const end = Math.min(total, start + state.pageSize - 1);
    els.resultCount.textContent = `Showing ${start}-${end} of ${total} ruilings.${semanticLabel}`;
  }

  function renderActiveFilters() {
    const pills = [];

    if (state.search) {
      pills.push(`<span class="active-filter-pill"><i class="bi bi-search" aria-hidden="true"></i>${escapeHtml(state.search)}</span>`);
    }

    if (state.category !== 'all') {
      pills.push(`<span class="active-filter-pill"><i class="bi bi-bookmark" aria-hidden="true"></i>${escapeHtml(state.category)}</span>`);
    }

    if (state.subCategory !== 'all') {
      pills.push(`<span class="active-filter-pill"><i class="bi bi-tags" aria-hidden="true"></i>${escapeHtml(state.subCategory)}</span>`);
    }

    if (state.stage !== 'all') {
      pills.push(`<span class="active-filter-pill"><i class="bi bi-diagram-3" aria-hidden="true"></i>${escapeHtml(state.stage)}</span>`);
    }

    if (state.court !== 'all') {
      pills.push(`<span class="active-filter-pill"><i class="bi bi-building" aria-hidden="true"></i>${escapeHtml(state.court)}</span>`);
    }

    els.activeFilters.innerHTML = pills.join('');
  }

  function fillSelect(element, values, defaultLabel) {
    if (!element) {
      return;
    }

    const options = [
      `<option value="all">${escapeHtml(defaultLabel)}</option>`,
      ...values.map((value) => `<option value="${escapeAttribute(value)}">${escapeHtml(value)}</option>`),
    ];

    element.innerHTML = options.join('');
  }

  function sortEntries(entries, sortKey) {
    const sorted = [...entries];

    switch (sortKey) {
      case 'serial-desc':
        sorted.sort((a, b) => (b.serial || 0) - (a.serial || 0));
        break;
      case 'year-desc':
        sorted.sort((a, b) => {
          const yb = b.year || 0;
          const ya = a.year || 0;
          if (yb !== ya) return yb - ya;
          return (a.serial || 0) - (b.serial || 0);
        });
        break;
      case 'year-asc':
        sorted.sort((a, b) => {
          const ya = a.year || 9999;
          const yb = b.year || 9999;
          if (ya !== yb) return ya - yb;
          return (a.serial || 0) - (b.serial || 0);
        });
        break;
      case 'serial-asc':
      default:
        sorted.sort((a, b) => (a.serial || 0) - (b.serial || 0));
        break;
    }

    return sorted;
  }

  function dedupeTags(tags) {
    const seen = new Set();
    const out = [];

    tags.forEach((tag) => {
      const cleanTag = String(tag || '').trim();
      if (!cleanTag) {
        return;
      }

      const normalized = cleanTag
        .toLowerCase()
        .replace(/\bsection\b/g, 'sec')
        .replace(/\s+/g, ' ')
        .replace(/[.,;]+$/g, '')
        .trim();

      if (!seen.has(normalized)) {
        seen.add(normalized);
        out.push(cleanTag);
      }
    });

    return out;
  }

  function uniqueSorted(items) {
    return [...new Set(items.filter(Boolean))].sort((a, b) => a.localeCompare(b));
  }

  function buildQuickTake(entry) {
    const issue = String(entry.issue || '').trim();
    const holding = String(entry.holding || '').trim();
    const source = holding || issue || 'Apply this citation as a supporting proposition after matching facts.';
    const sentenceMatch = source.match(/^.*?[.!?](\s|$)/);
    const firstSentence = sentenceMatch ? sentenceMatch[0].trim() : source;
    const compact = firstSentence.replace(/\s+/g, ' ').trim();
    if (compact.length <= 170) {
      return compact;
    }
    return `${compact.slice(0, 167).trim()}...`;
  }

  function buildPreviewText(text, maxChars) {
    const compact = String(text || '').replace(/\s+/g, ' ').trim();
    if (!compact) {
      return 'Details available in the full view.';
    }
    if (compact.length <= maxChars) {
      return compact;
    }
    return `${compact.slice(0, maxChars - 3).trim()}...`;
  }

  function buildAddPayload(caseReference, verdict, impact) {
    return {
      caseReference: oneLine(caseReference),
      verdict: oneLine(verdict),
      impact: oneLine(impact),
      category: (els.optionalCategory?.value || '').trim(),
      subCategory: (els.optionalSubCategory?.value || '').trim(),
      stage: (els.optionalStage?.value || '').trim(),
      court: (els.optionalCourt?.value || '').trim(),
      year: (els.optionalYear?.value || '').trim(),
      statuteTags: splitInputToList(els.optionalStatuteTags?.value || ''),
      advocateNotes: splitInputToList(els.optionalAdvocateNotes?.value || ''),
      relatedDetails: splitInputToList(els.optionalRelatedDetails?.value || ''),
    };
  }

  function splitInputToList(text) {
    return String(text || '')
      .split(/\n|,|;|\|/g)
      .map((item) => item.trim())
      .filter(Boolean);
  }

  function upsertEntryInState(entry) {
    const normalizedTags = dedupeTags(entry.statuteTags || []);
    const searchable = [
      entry.caseReference,
      entry.issue,
      entry.holding,
      entry.category,
      entry.subCategory,
      entry.court,
      entry.stage,
      normalizedTags.join(' '),
    ]
      .join(' ')
      .toLowerCase();

    const normalized = {
      ...entry,
      statuteTags: normalizedTags,
      searchable,
    };

    const id = Number(entry.id || entry.serial);
    const index = state.entries.findIndex((item) => Number(item.id || item.serial) === id);
    if (index >= 0) {
      state.entries[index] = normalized;
    } else {
      state.entries.push(normalized);
    }
    state.entriesById.set(id, normalized);
  }

  function resetAddForm() {
    if (els.newCaseReference) els.newCaseReference.value = '';
    if (els.newVerdict) els.newVerdict.value = '';
    if (els.newImpact) els.newImpact.value = '';
    if (els.optionalCategory) els.optionalCategory.value = '';
    if (els.optionalSubCategory) els.optionalSubCategory.value = '';
    if (els.optionalStage) els.optionalStage.value = '';
    if (els.optionalCourt) els.optionalCourt.value = '';
    if (els.optionalYear) els.optionalYear.value = '';
    if (els.optionalStatuteTags) els.optionalStatuteTags.value = '';
    if (els.optionalAdvocateNotes) els.optionalAdvocateNotes.value = '';
    if (els.optionalRelatedDetails) els.optionalRelatedDetails.value = '';
  }

  function showToast(title, message, tone) {
    if (!els.atlasToastContainer || !window.bootstrap?.Toast) {
      return;
    }

    const colorMap = {
      info: 'text-bg-primary',
      success: 'text-bg-success',
      warning: 'text-bg-warning',
      danger: 'text-bg-danger',
    };
    const toneClass = colorMap[tone] || colorMap.info;
    const toast = document.createElement('div');
    toast.className = `toast atlas-toast align-items-center ${toneClass}`;
    toast.setAttribute('role', 'status');
    toast.setAttribute('aria-live', 'polite');
    toast.setAttribute('aria-atomic', 'true');
    toast.innerHTML = `
      <div class="d-flex">
        <div class="toast-body">
          <strong>${escapeHtml(title)}</strong><br>
          ${escapeHtml(message)}
        </div>
        <button type="button" class="btn-close me-2 m-auto" data-bs-dismiss="toast" aria-label="Close"></button>
      </div>
    `;

    els.atlasToastContainer.appendChild(toast);
    const instance = new window.bootstrap.Toast(toast, { delay: 3600 });
    toast.addEventListener('hidden.bs.toast', () => {
      toast.remove();
    });
    instance.show();
  }

  function openRuilingModal(entryId) {
    if (!Number.isFinite(entryId) || entryId <= 0 || !els.viewRuilingModal) {
      return;
    }

    const entry = state.entriesById.get(entryId);
    if (!entry) {
      return;
    }

    currentViewEntry = entry;
    renderRuilingModalContent(entry);

    if (viewModalInstance) {
      viewModalInstance.show();
    }
  }

  function renderRuilingModalContent(entry) {
    const serialDisplay = `#${String(entry.serial || '').padStart(3, '0')}`;
    const year = entry.year ? String(entry.year) : 'Year not captured';
    const court = entry.court || 'Reported Court';
    const stage = entry.stage || 'General';
    const category = entry.category || 'General Litigation Principles';
    const subCategory = entry.subCategory || 'General';

    if (els.viewRuilingModalLabel) {
      els.viewRuilingModalLabel.textContent = `${serialDisplay} ${entry.caseReference || 'Ruiling details'}`;
    }

    if (els.viewRuilingMeta) {
      els.viewRuilingMeta.textContent = `${court} | ${year} | ${stage} | ${category} -> ${subCategory}`;
    }

    if (els.viewRuilingVerdict) {
      els.viewRuilingVerdict.textContent = entry.issue || 'Verdict text is not available.';
    }

    if (els.viewRuilingImpact) {
      els.viewRuilingImpact.textContent = entry.holding || 'Impact text is not available.';
    }

    renderViewTags(entry.statuteTags || []);
    renderViewNotes(entry.advocateNotes || []);
    renderViewRelatedDetails(entry.relatedDetails || []);
    renderViewSources(entry.researchSources || []);
    renderRelatedRuilings(entry);
  }

  function renderViewTags(tags) {
    if (!els.viewRuilingTags) {
      return;
    }

    const normalized = dedupeTags(tags).slice(0, 12);
    if (!normalized.length) {
      els.viewRuilingTags.innerHTML = '<span class="ruiling-tag">No section tags available in source</span>';
      return;
    }

    els.viewRuilingTags.innerHTML = normalized
      .map((tag) => `<span class="ruiling-tag">${escapeHtml(tag)}</span>`)
      .join('');
  }

  function renderViewNotes(notes) {
    if (!els.viewRuilingNotes) {
      return;
    }

    const normalized = safeTextList(notes, 4);
    if (!normalized.length) {
      els.viewRuilingNotes.innerHTML = '<li>No playbook note added yet. Start with factual parity, jurisdiction, and current binding status.</li>';
      return;
    }

    els.viewRuilingNotes.innerHTML = normalized
      .map((note) => `<li>${escapeHtml(note)}</li>`)
      .join('');
  }

  function renderViewRelatedDetails(details) {
    if (!els.viewRuilingRelatedDetails) {
      return;
    }

    const normalized = safeTextList(details, 6);
    if (!normalized.length) {
      els.viewRuilingRelatedDetails.innerHTML = '<li>No additional related details added yet for this entry.</li>';
      return;
    }

    els.viewRuilingRelatedDetails.innerHTML = normalized
      .map((item) => `<li>${escapeHtml(item)}</li>`)
      .join('');
  }

  function renderViewSources(sources) {
    if (!els.viewRuilingSources) {
      return;
    }

    const normalized = safeUrlList(sources, 8);
    if (!normalized.length) {
      els.viewRuilingSources.innerHTML = '<p class="m-0 text-muted">No web references captured for this entry yet.</p>';
      return;
    }

    els.viewRuilingSources.innerHTML = normalized
      .map((url, index) => (
        `<a class="view-source-link" href="${escapeAttribute(url)}" target="_blank" rel="noopener noreferrer">Source ${index + 1}: ${escapeHtml(shortUrl(url))}</a>`
      ))
      .join('');
  }

  function renderRelatedRuilings(entry) {
    if (!els.viewRuilingRelatedList || !els.viewRuilingRelatedHint) {
      return;
    }

    const relatedResult = getRelatedRuilings(entry, 4);
    const related = relatedResult.items;
    const isLlm = relatedResult.source === 'llm';

    if (isLlm) {
      els.viewRuilingRelatedHint.textContent = 'Model-ranked companion authorities for stronger citation strategy.';
    } else {
      els.viewRuilingRelatedHint.textContent = 'Similarity-ranked companion authorities (fallback ranking).';
    }

    if (!related.length) {
      els.viewRuilingRelatedList.innerHTML = '<div class="related-empty">No related ruilings found for this entry yet.</div>';
      return;
    }

    els.viewRuilingRelatedList.innerHTML = related
      .map((item) => {
        const itemId = Number(item.id || item.serial);
        const quickTake = buildQuickTake(item);
        const ref = escapeHtml(item.caseReference || '');
        const stage = escapeHtml(item.stage || 'General');
        const serial = String(item.serial || '').padStart(3, '0');

        return `
          <article class="related-ruiling-card" aria-label="Related ruiling ${serial}">
            <div class="related-ruiling-head">
              <span class="ruiling-serial">#${serial}</span>
              <span class="ruiling-stage">${stage}</span>
            </div>
            <p class="related-ruiling-ref">${ref}</p>
            <p class="related-ruiling-snippet">${escapeHtml(quickTake)}</p>
            <div class="related-ruiling-actions">
              <button type="button" class="mini-action-btn view-btn" data-open-entry-id="${itemId}">Open</button>
              <button type="button" class="mini-action-btn" data-copy-ref="${escapeAttribute(item.caseReference || '')}">Copy Citation</button>
            </div>
          </article>
        `;
      })
      .join('');
  }

  function getRelatedRuilings(entry, limit) {
    const baseId = Number(entry.id || entry.serial || 0);
    const rawRelated = state.relatedMap?.[baseId] || state.relatedMap?.[String(baseId)];

    if (Array.isArray(rawRelated) && rawRelated.length) {
      const llmItems = rawRelated
        .map((value) => state.entriesById.get(Number(value)))
        .filter(Boolean)
        .filter((item) => Number(item.id || item.serial) !== baseId)
        .slice(0, limit);

      if (llmItems.length) {
        return { source: 'llm', items: llmItems };
      }
    }

    return {
      source: 'fallback',
      items: getSimilarityRelatedRuilings(entry, limit),
    };
  }

  function getSimilarityRelatedRuilings(entry, limit) {
    const baseId = Number(entry.id || entry.serial || 0);
    const entryTags = new Set(dedupeTags(entry.statuteTags || []).map((tag) => tag.toLowerCase()));
    const baseKeywords = buildKeywordSet(
      [entry.issue, entry.holding, entry.subCategory, entry.category].join(' ')
    );

    const scored = state.entries
      .filter((candidate) => Number(candidate.id || candidate.serial) !== baseId)
      .map((candidate) => {
        let score = 0;
        if (candidate.category === entry.category) score += 6;
        if (candidate.subCategory === entry.subCategory) score += 4;
        if (candidate.stage === entry.stage) score += 2;
        if (candidate.court === entry.court) score += 1;

        const candidateTags = new Set(
          dedupeTags(candidate.statuteTags || []).map((tag) => tag.toLowerCase())
        );
        score += intersectionSize(entryTags, candidateTags) * 2;

        const candidateKeywords = buildKeywordSet(
          [candidate.issue, candidate.holding, candidate.subCategory, candidate.category].join(' ')
        );
        score += intersectionSize(baseKeywords, candidateKeywords) * 0.35;

        return { candidate, score };
      })
      .filter((item) => item.score > 0)
      .sort((a, b) => {
        if (b.score !== a.score) return b.score - a.score;
        return (a.candidate.serial || 0) - (b.candidate.serial || 0);
      })
      .slice(0, limit)
      .map((item) => item.candidate);

    return scored;
  }

  function buildKeywordSet(text) {
    const stopWords = new Set([
      'the',
      'and',
      'for',
      'with',
      'that',
      'from',
      'this',
      'into',
      'under',
      'while',
      'where',
      'which',
      'there',
      'shall',
      'would',
      'after',
      'before',
      'against',
      'case',
      'cases',
      'court',
      'section',
      'sections',
      'code',
      'act',
    ]);

    const words = String(text || '')
      .toLowerCase()
      .replace(/[^a-z0-9\s]/g, ' ')
      .split(/\s+/)
      .filter((word) => word.length > 2 && !stopWords.has(word));

    return new Set(words);
  }

  function intersectionSize(setA, setB) {
    if (!setA || !setB || !setA.size || !setB.size) {
      return 0;
    }

    let count = 0;
    setA.forEach((value) => {
      if (setB.has(value)) {
        count += 1;
      }
    });
    return count;
  }

  function safeTextList(value, maxLen) {
    if (!Array.isArray(value)) {
      return [];
    }

    const seen = new Set();
    const out = [];
    value.forEach((item) => {
      const text = String(item || '').replace(/\s+/g, ' ').trim();
      if (!text) {
        return;
      }

      const key = text.toLowerCase();
      if (seen.has(key)) {
        return;
      }

      seen.add(key);
      out.push(text);
      if (out.length >= maxLen) {
        return;
      }
    });
    return out.slice(0, maxLen);
  }

  function safeUrlList(value, maxLen) {
    if (!Array.isArray(value)) {
      return [];
    }

    const seen = new Set();
    const out = [];
    value.forEach((item) => {
      const text = String(item || '').trim();
      if (!text || !/^https?:\/\//i.test(text)) {
        return;
      }
      if (seen.has(text)) {
        return;
      }
      seen.add(text);
      out.push(text);
      if (out.length >= maxLen) {
        return;
      }
    });
    return out.slice(0, maxLen);
  }

  function shortUrl(url) {
    try {
      const parsed = new URL(url);
      const host = parsed.hostname.replace(/^www\./i, '');
      const path = parsed.pathname.length > 32 ? `${parsed.pathname.slice(0, 29)}...` : parsed.pathname;
      return `${host}${path}`;
    } catch (error) {
      return url;
    }
  }

  function escapeHtml(value) {
    return String(value || '')
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#39;');
  }

  function escapeAttribute(value) {
    return escapeHtml(value).replace(/`/g, '&#96;');
  }

  async function copyToClipboard(text) {
    try {
      if (navigator.clipboard && navigator.clipboard.writeText) {
        await navigator.clipboard.writeText(text);
        return true;
      }
    } catch (error) {
      // Fall through to legacy method
    }

    try {
      const textArea = document.createElement('textarea');
      textArea.value = text;
      textArea.setAttribute('readonly', '');
      textArea.style.position = 'absolute';
      textArea.style.left = '-9999px';
      document.body.appendChild(textArea);
      textArea.select();
      const successful = document.execCommand('copy');
      document.body.removeChild(textArea);
      return successful;
    } catch (error) {
      return false;
    }
  }

  function oneLine(text) {
    return String(text || '').replace(/\s+/g, ' ').trim();
  }

  function normalizeSearchQuery(text) {
    return oneLine(text).toLowerCase();
  }

  function debounce(fn, delay) {
    let timer = null;
    return function debounced(...args) {
      if (timer) {
        clearTimeout(timer);
      }
      timer = setTimeout(() => {
        fn.apply(this, args);
      }, delay);
    };
  }
})();
