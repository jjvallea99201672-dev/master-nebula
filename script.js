// Master Nebula — plain JS, no dependencies.
(function () {
  // Click-to-load YouTube embeds (fast on mobile, no tracking until played).
  document.querySelectorAll('.vid[data-yt]').forEach(function (el) {
    var id = el.dataset.yt, title = el.dataset.title || 'Video';
    el.style.backgroundImage = 'url(https://i.ytimg.com/vi/' + id + '/hqdefault.jpg)';
    el.innerHTML = '<button class="play" aria-label="Play: ' + title.replace(/"/g, '&quot;') + '"><i></i></button><span class="cap"></span>';
    el.querySelector('.cap').textContent = title;
    function load() {
      var f = document.createElement('iframe');
      f.src = 'https://www.youtube-nocookie.com/embed/' + id + '?autoplay=1&rel=0';
      f.title = title; f.allow = 'autoplay; encrypted-media; picture-in-picture; fullscreen'; f.allowFullscreen = true;
      el.innerHTML = ''; el.appendChild(f); el.style.cursor = 'default';
    }
    el.addEventListener('click', load, { once: true });
  });

  // Mobile menu
  var btn = document.querySelector('.menu-btn'), menu = document.getElementById('menu');
  btn.addEventListener('click', function () { var o = menu.classList.toggle('open'); btn.setAttribute('aria-expanded', o); });
  menu.addEventListener('click', function () { menu.classList.remove('open'); btn.setAttribute('aria-expanded', false); });

  // Gallery lightbox
  var lb = document.getElementById('lightbox'), lbi = lb.querySelector('img');
  document.querySelectorAll('.gallery button').forEach(function (b) {
    b.addEventListener('click', function () { lbi.src = b.dataset.full; lbi.alt = b.querySelector('img').alt; lb.hidden = false; });
  });
  lb.addEventListener('click', function () { lb.hidden = true; });
  document.addEventListener('keydown', function (e) { if (e.key === 'Escape') lb.hidden = true; });

  // Starfield
  var c = document.getElementById('stars'), x = c.getContext('2d'), stars = [], W, H;
  var still = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  function size() {
    var d = Math.min(window.devicePixelRatio || 1, 2);
    W = c.width = innerWidth * d; H = c.height = innerHeight * d;
    stars = []; var n = Math.round(innerWidth * innerHeight / 7000);
    for (var i = 0; i < n; i++) stars.push({ x: Math.random() * W, y: Math.random() * H, r: (Math.random() * 1.3 + .3) * d,
      p: Math.random() * 6.28, s: Math.random() * .02 + .005, g: Math.random() < .18 });
  }
  function draw(t) {
    x.clearRect(0, 0, W, H);
    for (var i = 0; i < stars.length; i++) {
      var s = stars[i], a = .45 + .55 * Math.sin(s.p + t * s.s * .06);
      x.globalAlpha = still ? .7 : a; x.fillStyle = s.g ? '#ffc83d' : '#9fe9ff';
      x.beginPath(); x.arc(s.x, s.y, s.r, 0, 6.28); x.fill();
    }
    if (!still) requestAnimationFrame(draw);
  }
  size(); addEventListener('resize', function () { size(); if (still) draw(0); });
  requestAnimationFrame(draw);
})();
