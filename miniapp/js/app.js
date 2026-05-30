// Nastya Mini-App 🎀
document.addEventListener('DOMContentLoaded', () => {
    // Animate chat bubbles with delay
    const bubbles = document.querySelectorAll('.chat-bubble');
    bubbles.forEach((bubble, i) => {
        bubble.style.opacity = '0';
        bubble.style.transform = 'translateY(20px)';
        setTimeout(() => {
            bubble.style.transition = 'all 0.5s ease';
            bubble.style.opacity = '1';
            bubble.style.transform = 'translateY(0)';
        }, 300 + i * 500);
    });

    // Random mood highlight
    const moodCards = document.querySelectorAll('.mood-card');
    if (moodCards.length) {
        const randomMood = moodCards[Math.floor(Math.random() * moodCards.length)];
        randomMood.style.background = 'linear-gradient(135deg, #fce4ec, #f8bbd0)';
        randomMood.style.transform = 'scale(1.05)';
    }

    // Nastya facts rotation
    const facts = [
        "Настя никогда не опаздывает — она задерживается! 😤",
        "Каждая третья Настя хочет стать психологом 🧠",
        "Насти верят в астрологию, но не в гороскопы из инета 🔮",
        "Если Настя говорит 'мне всё равно' — ей НЕ всё равно! 😤",
        "Настя может спорить с навигатором 🗺️",
    ];

    // Add rotating fact to header
    const subtitle = document.querySelector('.subtitle');
    if (subtitle) {
        let factIndex = 0;
        setInterval(() => {
            factIndex = (factIndex + 1) % facts.length;
            subtitle.style.transition = 'opacity 0.3s';
            subtitle.style.opacity = '0';
            setTimeout(() => {
                subtitle.textContent = facts[factIndex];
                subtitle.style.opacity = '1';
            }, 300);
        }, 4000);
    }

    console.log('🎀 Настя Mini-App loaded!');
});
