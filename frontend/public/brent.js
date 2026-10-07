(function () {
  var URL_ = '/brent.json', PERIOD = 60000;
  var lastGen = 0, lastGenTxt = '', failures = 0, timer = null;
  // the job runs every 5 min: stale after one missed run, dead after two
  var STALE_S = 420, DEAD_S = 900;
  var $ = function (id) { return document.getElementById(id); };

  function setHTML(id, html) {
    var el = $(id);
    if (el && typeof html === 'string') el.innerHTML = html;
  }

  function render(d) {
    var s = d.stats, h = d.html;
    $('sMark').textContent = s.mark;
    $('sMarkN').textContent = s.chg + '% on the day · ' + s.fromhigh + '% from the ' + s.high + ' high';
    $('sNShort').textContent = s.nshort;
    $('sNShortN').textContent = s.totshort.replace(/<[^>]+>/g, '') + ' notional';
    $('sUpnl').innerHTML = s.upnl;
    $('sNLong').textContent = s.nlong;
    $('sNLongN').textContent = s.totlong.replace(/<[^>]+>/g, '') + ' notional';
    $('sTrips').textContent = s.ntrips;
    $('sTripsN').textContent = s.nclosed + ' closed · ' + s.nlive + ' still open';
    $('mWallets').textContent = s.nwallets + ' wallets seen';
    $('mFills').textContent = s.nfills + ' fills held';
    $('mCov').textContent = 'coverage from ' + s.covfrom;
    setHTML('chart', h.chart);
    setHTML('shortRows', h.shortRows);
    setHTML('longRows', h.longRows);
    setHTML('tripRows', h.tripRows);
    setHTML('fillRows', h.fillRows);
    setHTML('dayRows', h.dayRows);
    $('fGen').textContent = 'generated ' + d.generated.replace('T', ' ').slice(0, 19) + ' UTC';
    lastGen = d.generated_ms || Date.now();
    lastGenTxt = new Date(lastGen).toISOString().slice(11, 19);
  }

  function tick() {
    // age of the DATA, not of the last fetch — a stale file must look stale
    var age = Math.round((Date.now() - lastGen) / 1000);
    var dot = $('dot'), txt = $('liveTxt');
    if (!lastGen) return;
    dot.className = 'dot on' + (age > DEAD_S ? ' dead' : age > STALE_S ? ' stale' : '');
    var rel = age < 90 ? age + 's ago' : age < 3600 ? Math.round(age / 60) + 'm ago'
      : Math.round(age / 3600) + 'h ago';
    txt.textContent = (age > STALE_S ? 'STALE · ' : '') + 'data as of ' + lastGenTxt + ' UTC · ' + rel;
  }

  function load() {
    fetch(URL_ + '?t=' + Date.now(), { cache: 'no-store' })
      .then(function (r) { if (!r.ok) throw new Error(r.status); return r.json(); })
      .then(function (d) { failures = 0; render(d); tick(); })
      .catch(function (e) {
        failures++;
        var dot = $('dot'), txt = $('liveTxt');
        if (dot) dot.className = 'dot dead';
        if (txt) txt.textContent = 'update failed (' + failures + ') — retrying'
          + (lastGenTxt ? ' · data as of ' + lastGenTxt + ' UTC' : '');
      });
  }

  function schedule() {
    clearInterval(timer);
    timer = setInterval(load, PERIOD);
  }

  // pause polling while the tab is hidden, refresh immediately on return
  document.addEventListener('visibilitychange', function () {
    if (document.hidden) { clearInterval(timer); }
    else { load(); schedule(); }
  });

  load();
  schedule();
  setInterval(tick, 1000);
})();
