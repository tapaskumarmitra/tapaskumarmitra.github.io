/**
 * Mitra Legal Services — main.js
 * Site: tapasmitra.com
 * Version: 2.0
 *
 * Sections:
 *  1. Preloader
 *  2. Navbar scroll effect
 *  3. Bootstrap Scrollspy
 *  4. Smooth scroll + mobile offcanvas close
 *  5. Scroll-triggered animations (Intersection Observer)
 *  6. Back-to-top button
 *  7. Scroll indicator fade
 *  8. Contact form validation & submission
 *  9. Copyright year
 */

'use strict';

/* ── 1. PRELOADER ─────────────────────────────────────────── */

window.addEventListener('load', () => {
  const preloader = document.getElementById('preloader');
  if (!preloader) return;
  preloader.style.opacity = '0';
  setTimeout(() => {
    preloader.remove();
  }, 550);
});


/* ── 2. NAVBAR SCROLL EFFECT ──────────────────────────────── */

(function initNavbar() {
  const nav = document.getElementById('mainNav');
  if (!nav) return;

  function handleNavbarScroll() {
    if (window.scrollY > 80) {
      nav.classList.add('scrolled');
    } else {
      nav.classList.remove('scrolled');
    }
  }

  window.addEventListener('scroll', handleNavbarScroll, { passive: true });
  handleNavbarScroll(); // Run on load in case page is already scrolled
})();


/* ── 3. BOOTSTRAP SCROLLSPY ───────────────────────────────── */

(function initScrollspy() {
  // Scrollspy is declared via HTML data attributes on the sections,
  // but we also need to keep the nav link .active class in sync.
  // Bootstrap 5 scrollspy requires the scrollable element to have the
  // data-bs-spy attribute; we do it imperatively for more control.

  const sections = document.querySelectorAll('section[id], div[id]');
  const navLinks = document.querySelectorAll('#mainNav .nav-link[href^="#"]');

  if (!sections.length || !navLinks.length) return;

  const navHeight = document.getElementById('mainNav')?.offsetHeight || 78;

  function onScroll() {
    let current = '';
    const scrollPos = window.scrollY + navHeight + 60;

    sections.forEach(section => {
      if (section.offsetTop <= scrollPos) {
        current = section.id;
      }
    });

    navLinks.forEach(link => {
      link.classList.remove('active');
      if (link.getAttribute('href') === '#' + current) {
        link.classList.add('active');
      }
    });
  }

  window.addEventListener('scroll', onScroll, { passive: true });
  onScroll();
})();


/* ── 4. SMOOTH SCROLL + OFFCANVAS CLOSE ───────────────────── */

(function initSmoothScroll() {
  // Bootstrap handles smooth scroll for href="#id" links via CSS scroll-behavior,
  // but we add this handler to also close the offcanvas on mobile nav clicks.

  const offcanvasEl = document.getElementById('navOffcanvas');
  let offcanvasInstance = null;

  if (offcanvasEl && typeof bootstrap !== 'undefined') {
    offcanvasInstance = bootstrap.Offcanvas.getOrCreateInstance(offcanvasEl);
  }

  document.querySelectorAll('a[href^="#"]').forEach(anchor => {
    anchor.addEventListener('click', function (e) {
      const targetId = this.getAttribute('href');
      if (!targetId || targetId === '#') return;

      const target = document.querySelector(targetId);
      if (!target) return;

      // Close offcanvas if open
      if (offcanvasInstance) {
        offcanvasInstance.hide();
      }

      // Smooth scroll with navbar offset
      const navHeight = document.getElementById('mainNav')?.offsetHeight || 78;
      const top = target.getBoundingClientRect().top + window.scrollY - navHeight;

      window.scrollTo({ top, behavior: 'smooth' });
      e.preventDefault();
    });
  });
})();


/* ── 5. SCROLL-TRIGGERED ANIMATIONS ──────────────────────── */

(function initScrollAnimations() {
  const elements = document.querySelectorAll('.animate-on-scroll');
  if (!elements.length) return;

  // Respect user's reduced-motion preference
  const prefersReducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;

  if (prefersReducedMotion) {
    elements.forEach(el => el.classList.add('animated'));
    return;
  }

  const observer = new IntersectionObserver((entries) => {
    entries.forEach(entry => {
      if (entry.isIntersecting) {
        const delay = parseInt(entry.target.dataset.delay || '0', 10);
        setTimeout(() => {
          entry.target.classList.add('animated');
        }, delay);
        observer.unobserve(entry.target);
      }
    });
  }, {
    threshold: 0.12,
    rootMargin: '0px 0px -40px 0px'
  });

  elements.forEach(el => observer.observe(el));
})();


/* ── 6. BACK-TO-TOP BUTTON ────────────────────────────────── */

(function initBackToTop() {
  const btn = document.getElementById('backToTop');
  if (!btn) return;

  function toggleVisibility() {
    if (window.scrollY > 300) {
      btn.classList.add('visible');
    } else {
      btn.classList.remove('visible');
    }
  }

  window.addEventListener('scroll', toggleVisibility, { passive: true });
  toggleVisibility();

  btn.addEventListener('click', () => {
    window.scrollTo({ top: 0, behavior: 'smooth' });
  });
})();


/* ── 7. SCROLL INDICATOR FADE ─────────────────────────────── */

(function initScrollIndicator() {
  const indicator = document.getElementById('scrollIndicator');
  if (!indicator) return;

  function updateOpacity() {
    const opacity = Math.max(0, 1 - window.scrollY / 280);
    indicator.style.opacity = opacity.toString();
    indicator.style.pointerEvents = opacity < 0.1 ? 'none' : '';
  }

  window.addEventListener('scroll', updateOpacity, { passive: true });
  updateOpacity();
})();


/* ── 8. CONTACT FORM ──────────────────────────────────────── */

(function initContactForm() {
  const form = document.getElementById('contact-form');
  if (!form) return;

  const submitBtn = document.getElementById('submit-btn');
  const feedbackEl = document.getElementById('form-feedback');

  // Field validation rules
  const validators = {
    'contact-name': {
      validate: v => v.trim().length >= 2,
      message: 'Please enter your full name (at least 2 characters).'
    },
    'contact-email': {
      validate: v => /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(v.trim()),
      message: 'Please enter a valid email address.'
    },
    'contact-subject': {
      validate: v => v.trim().length >= 3,
      message: 'Please enter a subject (at least 3 characters).'
    },
    'contact-message': {
      validate: v => v.trim().length >= 10,
      message: 'Please enter your message (at least 10 characters).'
    }
  };

  function showError(fieldId, message) {
    const field = document.getElementById(fieldId);
    const errorEl = document.getElementById(fieldId.replace('contact-', '') + '-error');
    if (field) field.classList.add('is-invalid');
    if (errorEl) errorEl.textContent = message;
  }

  function clearError(fieldId) {
    const field = document.getElementById(fieldId);
    const errorEl = document.getElementById(fieldId.replace('contact-', '') + '-error');
    if (field) {
      field.classList.remove('is-invalid');
      field.classList.remove('is-valid');
    }
    if (errorEl) errorEl.textContent = '';
  }

  function markValid(fieldId) {
    const field = document.getElementById(fieldId);
    if (field) {
      field.classList.remove('is-invalid');
      field.classList.add('is-valid');
    }
  }

  function validateForm() {
    let isValid = true;

    Object.entries(validators).forEach(([fieldId, rule]) => {
      const field = document.getElementById(fieldId);
      if (!field) return;
      const value = field.value;

      if (!rule.validate(value)) {
        showError(fieldId, rule.message);
        isValid = false;
      } else {
        clearError(fieldId);
        markValid(fieldId);
      }
    });

    return isValid;
  }

  // Live validation on blur
  Object.keys(validators).forEach(fieldId => {
    const field = document.getElementById(fieldId);
    if (!field) return;

    field.addEventListener('blur', () => {
      const rule = validators[fieldId];
      if (!rule.validate(field.value)) {
        showError(fieldId, rule.message);
      } else {
        clearError(fieldId);
        markValid(fieldId);
      }
    });

    field.addEventListener('input', () => {
      if (field.classList.contains('is-invalid')) {
        const rule = validators[fieldId];
        if (rule.validate(field.value)) {
          clearError(fieldId);
          markValid(fieldId);
        }
      }
    });
  });

  function setLoading(loading) {
    const normal = submitBtn.querySelector('.btn-text-normal');
    const loadingSpan = submitBtn.querySelector('.btn-text-loading');
    if (loading) {
      normal.classList.add('d-none');
      loadingSpan.classList.remove('d-none');
      submitBtn.disabled = true;
    } else {
      normal.classList.remove('d-none');
      loadingSpan.classList.add('d-none');
      submitBtn.disabled = false;
    }
  }

  function showFeedback(type, message) {
    feedbackEl.innerHTML = `
      <div class="alert alert-${type}" role="alert">
        <i class="bi bi-${type === 'success' ? 'check-circle-fill' : 'exclamation-triangle-fill'} me-2"></i>
        ${message}
      </div>
    `;
    feedbackEl.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
  }

  function clearFeedback() {
    feedbackEl.innerHTML = '';
  }

  form.addEventListener('submit', async (e) => {
    e.preventDefault();
    clearFeedback();

    if (!validateForm()) return;

    const action = form.getAttribute('action') || '';
    const isFormspreeConfigured = action.includes('formspree.io/f/') && !action.includes('YOUR_FORM_ID');

    if (!isFormspreeConfigured) {
      // Fallback: open default email client with prefilled fields
      const name    = document.getElementById('contact-name')?.value.trim() || '';
      const email   = document.getElementById('contact-email')?.value.trim() || '';
      const phone   = document.getElementById('contact-phone')?.value.trim() || '';
      const subject = document.getElementById('contact-subject')?.value.trim() || '';
      const message = document.getElementById('contact-message')?.value.trim() || '';

      const body = encodeURIComponent(
        `Name: ${name}\nEmail: ${email}${phone ? '\nPhone: ' + phone : ''}\n\nMessage:\n${message}`
      );

      window.location.href = `mailto:tapaskalyani.mitra@gmail.com?subject=${encodeURIComponent(subject)}&body=${body}`;

      showFeedback('success',
        'Your default email client has opened with a pre-filled message. ' +
        'To enable direct web submissions, configure Formspree in the contact form action attribute.'
      );
      return;
    }

    // Formspree submission via fetch
    setLoading(true);

    try {
      const formData = new FormData(form);
      const response = await fetch(action, {
        method: 'POST',
        body: formData,
        headers: { 'Accept': 'application/json' }
      });

      if (response.ok) {
        showFeedback('success',
          'Your message has been sent successfully. Tapas Kumar Mitra will be in touch shortly.'
        );
        form.reset();
        // Clear all validation states
        Object.keys(validators).forEach(fieldId => clearError(fieldId));
      } else {
        const data = await response.json().catch(() => ({}));
        const errMsg = data?.errors?.map(err => err.message).join(', ')
          || 'There was an error sending your message. Please try again or contact directly by phone.';
        showFeedback('danger', errMsg);
      }
    } catch {
      showFeedback('danger',
        'Network error. Please check your connection and try again, or contact directly at ' +
        '<a href="tel:+919433734997" style="color:inherit">+91 94337 34997</a>.'
      );
    } finally {
      setLoading(false);
    }
  });
})();


/* ── 9. COPYRIGHT YEAR ────────────────────────────────────── */

(function setYear() {
  const el = document.getElementById('year');
  if (el) el.textContent = new Date().getFullYear();
})();
