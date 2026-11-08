// Дурак Онлайн — Клиентский движок интерфейса

(function() {
    'use strict';

    // 1. ИНИЦИАЛИЗАЦИЯ ПОЛЬЗОВАТЕЛЯ И КОМНАТЫ
    const pathParts = window.location.pathname.split('/');
    const roomId = pathParts[pathParts.length - 1] || 'default';

    let tgUser = null;
    let userId = null;
    let userName = 'Игрок';
    let userAvatar = '';

    if (window.Telegram && window.Telegram.WebApp) {
        window.Telegram.WebApp.ready();
        window.Telegram.WebApp.expand();
        tgUser = window.Telegram.WebApp.initDataUnsafe && window.Telegram.WebApp.initDataUnsafe.user;
        if (tgUser) {
            userId = String(tgUser.id);
            userName = tgUser.first_name + (tgUser.last_name ? ' ' + tgUser.last_name : '');
            userAvatar = tgUser.photo_url || '';
        }
    }

    if (!userId) {
        userId = localStorage.getItem('durak_uid');
        if (!userId) {
            userId = 'guest_' + Math.floor(Math.random() * 899999 + 100000);
            localStorage.setItem('durak_uid', userId);
        }
        userName = localStorage.getItem('durak_uname') || ('Игрок ' + userId.slice(-4));
    }

    let currentVersion = 0;
    let currentState = null;
    let pollInterval = null;
    let soundEnabled = true;

    // ЗВУКОВЫЕ ЭФФЕКТЫ (Web Audio API)
    const audioCtx = (window.AudioContext || window.webkitAudioContext) ? new (window.AudioContext || window.webkitAudioContext)() : null;
    function playTone(freq, type, duration, gainVal) {
        if (!soundEnabled || !audioCtx) return;
        try {
            if (audioCtx.state === 'suspended') audioCtx.resume();
            const osc = audioCtx.createOscillator();
            const gain = audioCtx.createGain();
            osc.type = type || 'sine';
            osc.frequency.value = freq;
            gain.gain.setValueAtTime(gainVal || 0.1, audioCtx.currentTime);
            gain.gain.exponentialRampToValueAtTime(0.001, audioCtx.currentTime + duration);
            osc.connect(gain);
            gain.connect(audioCtx.destination);
            osc.start();
            osc.stop(audioCtx.currentTime + duration);
        } catch(e) {}
    }

    function soundCardPlay() { playTone(440, 'triangle', 0.08, 0.15); }
    function soundBito() { playTone(280, 'sine', 0.15, 0.2); }
    function soundTake() { playTone(180, 'sawtooth', 0.25, 0.15); }
    function soundWin() { playTone(600, 'sine', 0.3, 0.2); setTimeout(() => playTone(880, 'sine', 0.4, 0.2), 150); }

    // DOM ЭЛЕМЕНТЫ
    const lblRoomId = document.getElementById('lbl-room-id');
    const lblGameState = document.getElementById('lbl-game-state');
    const opponentsLayer = document.getElementById('opponents-layer');
    const boutSlots = document.getElementById('bout-slots');
    const imgTrumpCard = document.getElementById('img-trump-card');
    const imgDeckTop = document.getElementById('img-deck-top');
    const deckBadge = document.getElementById('deck-badge');
    const trumpSuitIndicator = document.getElementById('trump-suit-indicator');
    const discardBadge = document.getElementById('discard-badge');
    const announcementBanner = document.getElementById('announcement-banner');

    const playerHand = document.getElementById('player-hand');
    const playerNameBadge = document.getElementById('player-name-badge');
    const playerRoleBadge = document.getElementById('player-role-badge');
    const cardsCountBadge = document.getElementById('cards-count-badge');

    const btnPass = document.getElementById('btn-pass');
    const btnTake = document.getElementById('btn-take');
    const btnAddBot = document.getElementById('btn-add-bot');
    const btnStart = document.getElementById('btn-start');
    const btnReactions = document.getElementById('btn-reactions');
    const btnSound = document.getElementById('btn-sound');

    const chatDrawer = document.getElementById('chat-drawer');
    const btnChatToggle = document.getElementById('btn-chat-toggle');
    const btnCloseChat = document.getElementById('btn-close-chat');
    const chatBadge = document.getElementById('chat-badge');
    const chatMessagesList = document.getElementById('chat-messages-list');
    const chatInput = document.getElementById('chat-input');
    const btnSendChat = document.getElementById('btn-send-chat');

    const settingsModal = document.getElementById('settings-modal');
    const btnSettings = document.getElementById('btn-settings');
    const btnCloseSettings = document.getElementById('btn-close-settings');
    const btnSaveSettings = document.getElementById('btn-save-settings');
    const selectBotModel = document.getElementById('select-bot-model');
    const shareLinkInput = document.getElementById('share-link-input');
    const btnCopyLink = document.getElementById('btn-copy-link');

    const gameOverModal = document.getElementById('game-over-modal');
    const gameOverTitle = document.getElementById('game-over-title');
    const gameOverContent = document.getElementById('game-over-content');
    const btnRestartGame = document.getElementById('btn-restart-game');

    lblRoomId.textContent = roomId;
    shareLinkInput.value = window.location.href;

    // ВХОД В КОМНАТУ
    async function joinRoom() {
        try {
            await fetch(`/api/durak/${roomId}/join`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    user_id: userId,
                    name: userName,
                    avatar: userAvatar
                })
            });
            fetchState();
        } catch (e) {
            console.error('Ошибка входа в комнату:', e);
        }
    }

    // ПОЛУЧЕНИЕ СОСТОЯНИЯ
    async function fetchState() {
        try {
            const resp = await fetch(`/api/durak/${roomId}/state?uid=${userId}&v=${currentVersion}`);
            if (!resp.ok) return;
            const data = await resp.json();
            if (data.version !== currentVersion) {
                currentVersion = data.version;
                renderGame(data);
            }
        } catch (e) {
            console.error('Ошибка запроса состояния:', e);
        }
    }

    // РЕНДЕР ИГРЫ
    function renderGame(state) {
        currentState = state;

        // Статус
        lblGameState.textContent = state.state === 'playing' ? 'Игра идет' : (state.state === 'finished' ? 'Завершена' : 'Ожидание');

        // Колода и козырь
        if (state.trump_card) {
            imgTrumpCard.src = `/static/cards/${state.trump_card}.svg`;
            imgTrumpCard.style.display = 'block';
            trumpSuitIndicator.textContent = getSuitSymbol(state.trump_suit) + ' ' + getSuitName(state.trump_suit);
        } else {
            imgTrumpCard.style.display = 'none';
            trumpSuitIndicator.textContent = '';
        }

        deckBadge.textContent = state.deck_count;
        if (state.deck_count <= 0) {
            imgDeckTop.style.visibility = 'hidden';
            deckBadge.textContent = '0';
        } else {
            imgDeckTop.style.visibility = 'visible';
        }

        // Сброс (Бито)
        discardBadge.textContent = state.discard_count;

        // Мой статус и карты
        renderMyHand(state);

        // Оппоненты
        renderOpponents(state);

        // Стол (карты атаки и защиты)
        renderBoutTable(state);

        // Кнопки действий
        renderActionButtons(state);

        // Чат и сообщения
        renderChat(state.chat_messages);

        // Окно победы/поражения
        if (state.state === 'finished') {
            showGameOver(state);
        } else {
            gameOverModal.classList.add('hidden');
        }
    }

    // РЕНДЕР КАРТ ИГРОКА
    function renderMyHand(state) {
        playerNameBadge.textContent = userName;
        const myPlayer = state.players.find(p => p.id === userId);

        if (!myPlayer) {
            cardsCountBadge.textContent = '';
            playerHand.innerHTML = '';
            return;
        }

        cardsCountBadge.textContent = `${state.my_cards.length} карт`;

        let roleText = 'Игрок';
        if (state.state === 'playing') {
            if (myPlayer.is_attacker) roleText = '⚔️ Атакует';
            else if (myPlayer.is_defender) roleText = '🛡️ Защищается';
            else if (myPlayer.has_passed) roleText = '✋ Бито';
            else if (myPlayer.took_cards) roleText = '📥 Взял карты';
        }
        playerRoleBadge.textContent = roleText;

        // Сортировка карт: не-козырные по мастям, козырные в конце справа
        const cards = [...state.my_cards];
        const trumpSuit = state.trump_suit;
        cards.sort((a, b) => {
            const aIsTrump = a.slice(-1) === trumpSuit;
            const bIsTrump = b.slice(-1) === trumpSuit;
            if (aIsTrump !== bIsTrump) return aIsTrump ? 1 : -1;
            if (a.slice(-1) !== b.slice(-1)) return a.slice(-1).localeCompare(b.slice(-1));
            return getCardValue(a) - getCardValue(b);
        });

        playerHand.innerHTML = '';
        cards.forEach(card => {
            const el = document.createElement('div');
            el.className = 'my-card-item';
            el.dataset.card = card;

            const isPlayable = checkIfPlayable(card, state, myPlayer);
            if (isPlayable) el.classList.add('playable');

            el.innerHTML = `<img src="/static/cards/${card}.svg" alt="${card}" loading="lazy">`;

            el.addEventListener('click', () => {
                onCardClick(card, state, myPlayer);
            });

            playerHand.appendChild(el);
        });
    }

    // ПРОВЕРКА ВОЗМОЖНОСТИ ХОДА КАРТОЙ
    function checkIfPlayable(card, state, myPlayer) {
        if (state.state !== 'playing' || myPlayer.status !== 'active') return false;

        const defender = state.players.find(p => p.id === state.defender_id);
        if (!defender) return false;

        // Защитник
        if (myPlayer.is_defender) {
            if (myPlayer.took_cards) return false;
            // Есть ли хоть одна неотбитая карта, которую эта карта бьет?
            return state.table.some(slot => slot.defense === null && canBeat(slot.attack, card, state.trump_suit));
        }

        // Атакующий / подкидывающий
        const unbeatenCount = state.table.filter(s => s.defense === null).length;
        if (state.table.length >= 6 || unbeatenCount >= defender.card_count) return false;

        if (state.table.length === 0) {
            return myPlayer.is_attacker;
        } else {
            // Подкидывание
            const tableRanks = new Set();
            state.table.forEach(s => {
                tableRanks.add(getCardRank(s.attack));
                if (s.defense) tableRanks.add(getCardRank(s.defense));
            });
            return tableRanks.has(getCardRank(card));
        }
    }

    // КЛИК ПО КАРТЕ
    async function onCardClick(card, state, myPlayer) {
        if (!checkIfPlayable(card, state, myPlayer)) return;

        soundCardPlay();
        try {
            await fetch(`/api/durak/${roomId}/action`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    user_id: userId,
                    action: 'play_card',
                    card: card
                })
            });
            fetchState();
        } catch (e) {
            console.error('Ошибка хода картой:', e);
        }
    }

    // РЕНДЕР ОППОНЕНТОВ
    function renderOpponents(state) {
        opponentsLayer.innerHTML = '';
        const opponents = state.players.filter(p => p.id !== userId);

        opponents.forEach(p => {
            const seat = document.createElement('div');
            seat.className = 'opponent-seat';
            seat.id = `seat-${p.id}`;

            const isTurn = (state.state === 'playing') && (
                (p.is_attacker && state.table.length === 0) ||
                (p.is_defender && state.table.some(s => s.defense === null))
            );

            let roleClass = '';
            let roleText = '';
            if (p.status === 'out') {
                roleClass = 'winner';
                roleText = `👑 #${p.win_place}`;
            } else if (p.is_attacker) {
                roleClass = 'attacking';
                roleText = '⚔️ Атака';
            } else if (p.is_defender) {
                roleClass = 'defending';
                roleText = '🛡️ Защита';
            } else if (p.has_passed) {
                roleClass = 'passed';
                roleText = '✋ Бито';
            } else if (p.took_cards) {
                roleClass = 'took';
                roleText = '📥 Взял';
            }

            // Веер рубашек
            const cardCount = p.card_count || 0;
            let cardsFanHtml = '';
            const fanCount = Math.min(cardCount, 6);
            for (let i = 0; i < fanCount; i++) {
                const rot = (i - (fanCount - 1) / 2) * 8;
                const shiftX = (i - (fanCount - 1) / 2) * 10;
                cardsFanHtml += `<img src="/static/cards/RED_BACK.svg" class="mini-card-back" style="transform: translateX(${shiftX}px) rotate(${rot}deg);" alt="card">`;
            }

            seat.innerHTML = `
                <div class="avatar-wrapper ${isTurn ? 'active-turn' : ''}">
                    ${p.avatar ? `<img src="${p.avatar}" class="avatar-img" alt="avatar">` : `<span class="avatar-placeholder">${p.is_bot ? '🤖' : '👤'}</span>`}
                </div>
                <div class="seat-name">${escapeHtml(p.name)}</div>
                ${roleText ? `<div class="seat-role-badge ${roleClass}">${roleText}</div>` : ''}
                <div class="opponent-cards-fan">
                    ${cardsFanHtml}
                    <div class="opp-cards-badge">${cardCount}</div>
                </div>
            `;
            opponentsLayer.appendChild(seat);
        });
    }

    // РЕНДЕР СТОЛА БОЯ (6 ПАР КАРТ)
    function renderBoutTable(state) {
        boutSlots.innerHTML = '';
        state.table.forEach((slot, index) => {
            const slotEl = document.createElement('div');
            slotEl.className = 'bout-slot';

            let html = `<div class="card-attack"><img src="/static/cards/${slot.attack}.svg" class="card-img" alt="${slot.attack}"></div>`;

            if (slot.defense) {
                html += `<div class="card-defense"><img src="/static/cards/${slot.defense}.svg" class="card-img" alt="${slot.defense}"></div>`;
            } else {
                // Если я защитник, могу кликнуть по слоту, чтобы побить выбранной картой
                slotEl.style.cursor = 'pointer';
                slotEl.addEventListener('click', () => {
                    const selected = document.querySelector('.my-card-item.selected');
                    if (selected) {
                        const card = selected.dataset.card;
                        onDefendSpecificSlot(card, index);
                    }
                });
            }

            slotEl.innerHTML = html;
            boutSlots.appendChild(slotEl);
        });

        // Если слотов меньше 6 и идет игра, показываем легкий плейсхолдер
        if (state.state === 'playing' && state.table.length < 6) {
            const emptySlot = document.createElement('div');
            emptySlot.className = 'bout-slot';
            emptySlot.innerHTML = `<div class="slot-placeholder"></div>`;
            boutSlots.appendChild(emptySlot);
        }
    }

    async function onDefendSpecificSlot(card, targetIndex) {
        soundCardPlay();
        try {
            await fetch(`/api/durak/${roomId}/action`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    user_id: userId,
                    action: 'play_card',
                    card: card,
                    target_index: targetIndex
                })
            });
            fetchState();
        } catch (e) {
            console.error('Ошибка защиты:', e);
        }
    }

    // КНОПКИ ДЕЙСТВИЙ
    function renderActionButtons(state) {
        const myPlayer = state.players.find(p => p.id === userId);

        btnPass.classList.add('hidden');
        btnTake.classList.add('hidden');
        btnAddBot.classList.add('hidden');
        btnStart.classList.add('hidden');

        if (state.state === 'waiting') {
            btnStart.classList.remove('hidden');
            if (state.players.length < 6) btnAddBot.classList.remove('hidden');
        } else if (state.state === 'playing' && myPlayer && myPlayer.status === 'active') {
            if (myPlayer.is_defender) {
                if (state.table.length > 0 && !myPlayer.took_cards) {
                    btnTake.classList.remove('hidden');
                }
            } else {
                // Атакующий / подкидывающий
                if (state.table.length > 0) {
                    btnPass.classList.remove('hidden');
                }
            }
        }
    }

    // ЧАТ И РЕАКЦИИ
    let lastRenderedChatCount = 0;
    function renderChat(messages) {
        if (!messages) return;

        chatMessagesList.innerHTML = '';
        messages.forEach(m => {
            const row = document.createElement('div');
            row.className = 'chat-bubble-row';
            row.innerHTML = `
                <div class="chat-sender">${escapeHtml(m.sender_name)} <span style="font-size:10px;color:#888;">${m.time}</span></div>
                <div class="chat-text">${escapeHtml(m.text)}</div>
            `;
            chatMessagesList.appendChild(row);
        });

        chatMessagesList.scrollTop = chatMessagesList.scrollHeight;

        // Показываем всплывающие реплики над аватарами
        if (messages.length > lastRenderedChatCount) {
            const newMsgs = messages.slice(lastRenderedChatCount);
            newMsgs.forEach(m => showSpeechBubble(m.sender_id, m.text));
            lastRenderedChatCount = messages.length;
        }
    }

    function showSpeechBubble(senderId, text) {
        let seatEl = document.getElementById(`seat-${senderId}`);
        if (!seatEl && senderId === userId) {
            seatEl = document.querySelector('.player-zone');
        }
        if (!seatEl) return;

        const bubble = document.createElement('div');
        bubble.className = 'speech-bubble';
        bubble.textContent = text;
        seatEl.appendChild(bubble);

        setTimeout(() => {
            bubble.style.transition = 'opacity 0.4s, transform 0.4s';
            bubble.style.opacity = '0';
            bubble.style.transform = 'translateY(-10px)';
            setTimeout(() => bubble.remove(), 400);
        }, 3200);
    }

    // ОКНО ИТОГОВ ИГРЫ
    function showGameOver(state) {
        gameOverModal.classList.remove('hidden');
        const loser = state.players.find(p => p.id === state.loser_id);
        const winners = state.winner_order.map((wid, idx) => {
            const wp = state.players.find(p => p.id === wid);
            return `${idx + 1}. 👑 <b>${wp ? escapeHtml(wp.name) : 'Игрок'}</b>`;
        }).join('<br>');

        if (loser) {
            if (loser.id === userId) {
                gameOverTitle.textContent = '🤡 Вы остались в Дураках!';
                gameOverContent.innerHTML = `В этой раздаче удача была не на вашей стороне.<br><br><b>Победители:</b><br>${winners}`;
            } else {
                gameOverTitle.textContent = '🎉 Победа!';
                soundWin();
                gameOverContent.innerHTML = `Дурак партии: 🤡 <b>${escapeHtml(loser.name)}</b>!<br><br><b>Победители:</b><br>${winners}`;
            }
        } else {
            gameOverTitle.textContent = '🤝 Боевая ничья!';
            gameOverContent.innerHTML = `Все игроки успешно вышли из игры!`;
        }
    }

    // ОБРАБОТЧИКИ КНОПОК
    btnStart.addEventListener('click', async () => {
        try {
            await fetch(`/api/durak/${roomId}/start`, { method: 'POST' });
            fetchState();
        } catch (e) {}
    });

    btnAddBot.addEventListener('click', async () => {
        try {
            await fetch(`/api/durak/${roomId}/add_bot`, { method: 'POST' });
            fetchState();
        } catch (e) {}
    });

    btnPass.addEventListener('click', async () => {
        soundBito();
        try {
            await fetch(`/api/durak/${roomId}/action`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ user_id: userId, action: 'pass' })
            });
            fetchState();
        } catch (e) {}
    });

    btnTake.addEventListener('click', async () => {
        soundTake();
        try {
            await fetch(`/api/durak/${roomId}/action`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ user_id: userId, action: 'take' })
            });
            fetchState();
        } catch (e) {}
    });

    btnRestartGame.addEventListener('click', async () => {
        gameOverModal.classList.add('hidden');
        try {
            await fetch(`/api/durak/${roomId}/start`, { method: 'POST' });
            fetchState();
        } catch (e) {}
    });

    btnSound.addEventListener('click', () => {
        soundEnabled = !soundEnabled;
        btnSound.textContent = soundEnabled ? '🔊' : '🔇';
    });

    btnChatToggle.addEventListener('click', () => chatDrawer.classList.toggle('hidden'));
    btnReactions.addEventListener('click', () => chatDrawer.classList.toggle('hidden'));
    btnCloseChat.addEventListener('click', () => chatDrawer.classList.add('hidden'));

    btnSendChat.addEventListener('click', sendUserMessage);
    chatInput.addEventListener('keydown', (e) => { if (e.key === 'Enter') sendUserMessage(); });

    async function sendUserMessage() {
        const text = chatInput.value.trim();
        if (!text) return;
        chatInput.value = '';
        try {
            await fetch(`/api/durak/${roomId}/action`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ user_id: userId, action: 'chat', text: text, sender_name: userName })
            });
            fetchState();
        } catch (e) {}
    }

    document.querySelectorAll('.emoji-btn, .phrase-btn').forEach(btn => {
        btn.addEventListener('click', async () => {
            const text = btn.textContent;
            try {
                await fetch(`/api/durak/${roomId}/action`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ user_id: userId, action: 'chat', text: text, sender_name: userName })
                });
                chatDrawer.classList.add('hidden');
                fetchState();
            } catch (e) {}
        });
    });

    // НАСТРОЙКИ
    btnSettings.addEventListener('click', () => settingsModal.classList.remove('hidden'));
    btnCloseSettings.addEventListener('click', () => settingsModal.classList.add('hidden'));
    btnCopyLink.addEventListener('click', () => {
        shareLinkInput.select();
        document.execCommand('copy');
        btnCopyLink.textContent = 'Скопировано!';
        setTimeout(() => btnCopyLink.textContent = 'Скопировать', 1500);
    });

    btnSaveSettings.addEventListener('click', async () => {
        const selectedModel = selectBotModel.value;
        try {
            await fetch(`/api/durak/${roomId}/set_model`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ bot_model: selectedModel })
            });
            settingsModal.classList.add('hidden');
            fetchState();
        } catch (e) {}
    });

    // ХЕЛПЕРЫ
    function getCardRank(card) { return card.slice(0, -1); }
    function getCardSuit(card) { return card.slice(-1); }
    function getCardValue(card) {
        const r = getCardRank(card);
        const map = {'6':6, '7':7, '8':8, '9':9, '10':10, 'J':11, 'Q':12, 'K':13, 'A':14};
        return map[r] || 0;
    }
    function canBeat(atk, def, trump) {
        const as = getCardSuit(atk), ds = getCardSuit(def);
        const av = getCardValue(atk), dv = getCardValue(def);
        if (ds === as) return dv > av;
        if (ds === trump && as !== trump) return true;
        return false;
    }
    function getSuitSymbol(s) {
        const map = {'S':'♠', 'H':'♥', 'D':'♦', 'C':'♣'};
        return map[s] || s;
    }
    function getSuitName(s) {
        const map = {'S':'Пики', 'H':'Черви', 'D':'Бубны', 'C':'Трефы'};
        return map[s] || s;
    }
    function escapeHtml(str) {
        if (!str) return '';
        return str.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
    }

    // СТАРТ ПОЛЛИНГА
    joinRoom();
    pollInterval = setInterval(fetchState, 1200);

})();
