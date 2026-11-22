# DeltaruneOnline

Patcher, który z oryginalnych plików pobranych przez użytkownika buduje **lokalnie** wersję Deltarune z modem
[Local Multiplayer](https://gamebanana.com/mods/601376) (Yomsman), w której gracze grają przez sieć,
a każdy uruchamia grę u siebie.

## Zasady repozytorium

- **Nie wrzucamy** plików gry (`data.win`, muzyki, wideo), plików moda (`*.xdelta`, zdekompilowany kod moda)
  ani gotowych buildów. Repo zawiera tylko własny kod i reguły zmian; wynik powstaje na komputerze użytkownika.
- Mod jest na licencji **CC BY-NC-ND 4.0**. Na stronie moda udostępnianie przeróbek i użycie fragmentów
  oznaczono jako „ask”, użytek komercyjny jako „no”. Repo zostaje **prywatne**, dopóki autor się nie zgodzi.
- Każdy gracz potrzebuje własnej kopii Deltarune i musi sam pobrać moda.

## Założenie techniczne: lockstep na wejściu

Obie strony uruchamiają pełną grę z tym samym zapisem i co klatkę wymieniają tylko stan przycisków
(z małym opóźnieniem wejścia). Gra liczy się u obu identycznie, więc nie trzeba synchronizować obiektów.

Etapy:

1. **Wirtualne wejście**: wszystkie odczyty klawiatury i pada (gra i mod) przez jedną warstwę,
   która dla graczy zdalnych zwraca przyciski z sieci.
2. **Transport**: funkcje sieciowe GameMakera (`network_*`), ramka = numer klatki + przyciski graczy.
3. **Deterministyczność**: wspólne ziarno losowania, logika niezależna od stanu dźwięku i zegara systemowego.
4. **Wykrywanie rozjazdów**: co N klatek skrót stanu (pozycje, ziarno losowania, flagi) i porównanie.
5. **Patcher**: UndertaleModTool CLI + skrypty, które nakładają zmiany na pliki użytkownika.

## Ryzyka (z przeglądu kodu rozdziału 5, łatka 0.253 + mod v19)

- losowanie w ok. 660 plikach kodu,
- ok. 36 plików zależnych od stanu dźwięku (np. minigra rytmiczna),
- ok. 52 pliki korzystające z zegara systemowego,
- sterowanie czytane bezpośrednio w wielu miejscach gry i moda,
- każdy rozdział to osobny `data.win`; zmiany trzeba powtarzać po aktualizacjach gry i moda.

Plan: prototyp na rozdziale 5, najpierw test na dwóch kopiach gry na jednym komputerze.

## Status

Szkielet repozytorium, kodu jeszcze nie ma.
