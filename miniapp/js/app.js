document.addEventListener('DOMContentLoaded', function() {
    const wants = [
        "Настя хочет шоколадку... Нет, мороженку! Нет, и то и то!",
        "Насте нужен новый айфон! Этот уже второй день как вышел!",
        "Хочу на Бали... Или на Пхукет... Ладно, куда угодно!",
        "Настя хочет ремонт... Обои, плитку, и вообще всё!",
        "Хочу вафельку с кремом! И латте на кокосовом!",
        "Настя сегодня на йогу! ...Ну ладно, на диван.",
        "Хочу щенка! Или котёнка. Нет, обоих!",
        "Настя хочет худеть... Нет, пироженку. Ладно, пироженку!",
        "Хочу пасту карбонара! Настя умеет готовить... почти.",
        "Насте нужен робот-пылесос! Я не пылесошу, но хочу!",
        "Хочу кроссовки... Белые. Нет, розовые! Все!",
        "Настя хочет спать... Но сначала сериал. И шоколадку.",
    ];
    const wantEl = document.getElementById('nastya-want');
    if (wantEl) {
        wantEl.textContent = wants[Math.floor(Math.random() * wants.length)];
        setInterval(function() {
            wantEl.style.opacity = '0';
            setTimeout(function() {
                wantEl.textContent = wants[Math.floor(Math.random() * wants.length)];
                wantEl.style.opacity = '1';
            }, 300);
        }, 5000);
    }
});
