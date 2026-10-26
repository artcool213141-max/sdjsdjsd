// Общий код всех страниц ивента «Кровавая Жатва»
const tg = window.Telegram.WebApp;
const BACKEND_URL = 'https://sdjsdjsd.vercel.app';

// Публичные ключи Supabase (anon-ключ можно светить, service_role — НИКОГДА)
const SUPABASE_URL = 'https://xbklrdpguqsuvqndcujb.supabase.co';
const SUPABASE_ANON_KEY = 'eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6Inhia2xyZHBndXFzdXZxbmRjdWpiIiwicm9sZSI6ImFub24iLCJpYXQiOjE3OTE2MzQ0NzUsImV4cCI6MjEwNzIxMDQ3NX0.IeK3NGOTu_J1jDOd_XjHQUOySnkbFh4uThGCPeu-Wu4';

// ---------------------------------------------------------------------
// ВСЕ иконки ивента в одном месте. Сейчас — эмодзи-заглушки.
// Когда будут картинки, меняется ТОЛЬКО функция ico() ниже:
//   return `<img src="img/ev_${key}.png" class="emo" style="width:${size}px;height:${size}px" alt="">`;
// ---------------------------------------------------------------------
const EMO = {
    pumpkin:  '🎃',   // кнопка/баннер ивента, заголовки
    soul:     '🩸',   // валюта «Кровавые Души»
    altar:    '🪦',   // Алтарь
    battle:   '⚔️',   // Онлайн-Битва
    cauldron: '🩸',   // Кровавый Котёл
    rating:   '🏆',   // Рейтинг Жнецов
    shield:   '🛡️',  // Тыквенный Щит
    skull:    '💀',   // соперник / поражение
    candle:   '🕯️',  // заклинание «Свеча»
    ghost:    '👻',   // заклинание «Призрак»
    curse:    '🔮'    // заклинание «Проклятие»
};
function ico(key, size) {
    const s = size || 20;
    return `<img src="img/ev_${key}.png" class="emo" style="width:${s}px;height:${s}px" alt="">`;
}

function esc(s) {
    return String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}
function $(id) { return document.getElementById(id); }
function haptic(t) { try { tg.HapticFeedback && tg.HapticFeedback.impactOccurred(t || 'light'); } catch (_) {} }
function fmtTime(sec) {
    sec = Math.max(0, Math.floor(sec));
    const h = Math.floor(sec / 3600), m = Math.floor((sec % 3600) / 60), s = sec % 60;
    const p = n => String(n).padStart(2, '0');
    return h ? `${h}:${p(m)}:${p(s)}` : `${p(m)}:${p(s)}`;
}
function toast(text) {
    let el = $('toast');
    if (!el) { el = document.createElement('div'); el.id = 'toast'; el.className = 'toast'; document.body.appendChild(el); }
    el.textContent = text;
    el.classList.add('show');
    clearTimeout(el._t);
    el._t = setTimeout(() => el.classList.remove('show'), 2600);
}

async function api(path, body) {
    const res = await fetch(BACKEND_URL + path, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ init_data: tg.initData, ...(body || {}) })
    });
    let data = {};
    try { data = await res.json(); } catch (_) {}
    if (!res.ok) throw new Error(data.detail || ('HTTP ' + res.status));
    return data;
}

// нижняя навигация между подстраницами ивента
function renderNav(active) {
    const items = [
        ['event.html', 'pumpkin', 'Хаб'],
        ['event_altar.html', 'altar', 'Алтарь'],
        ['event_battle.html', 'battle', 'Битва'],
        ['event_cauldron.html', 'cauldron', 'Котёл'],
        ['event_rating.html', 'rating', 'Рейтинг']
    ];
    $('nav').innerHTML = items.map(([href, k, label]) =>
        `<a href="${href}" class="${active === k ? 'on' : ''}">${ico(k, 20)}<span>${label}</span></a>`
    ).join('');
}

// шапка: назад + заголовок + баланс душ
function renderTop(backHref, title, withSouls) {
    $('top').innerHTML =
        `<a class="back" href="${backHref}" aria-label="Назад">‹</a>` +
        `<h1>${title}</h1>` +
        (withSouls ? `<div class="pill">${ico('soul', 15)}<span id="souls">0</span></div>` : '');
}
function setSouls(v) { const el = $('souls'); if (el) el.textContent = v; }

function initEvent(active, cb) {
    try { tg.expand(); tg.ready(); tg.headerColor = '#3a1500'; tg.backgroundColor = '#1a0700'; } catch (_) {}
    renderNav(active);
    if (!tg.initData) {
        $('view').innerHTML = '<div class="status">Открой приложение через Telegram</div>';
        return;
    }
    cb();
}
function showError(e) {
    console.error(e);
    $('view').innerHTML = '<div class="status">Не удалось загрузить данные: ' + esc(e.message || 'ошибка сети') + '</div>';
}
