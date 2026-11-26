# What you need to know about DELTARUNE (chapter 5, Local Multiplayer mod)

The program sends this text with every turn. "state" means the lines under "State now".

## Buttons

- **confirm** (Z or Enter on a keyboard): talk to someone or use an object that you stand next to and face; pick
  the option under a cursor; go on in a dialogue box that waits.
- **cancel** (X or Shift): go back one level in a menu; on the top level it closes the menu. In a dialogue box that
  is still typing, it shows the rest of the text at once.
- **menu** (C or Ctrl): opens the party menu, but only while you can walk (can_move=true), never during a
  dialogue, a cutscene or another menu. In the Dark World this is the Dark World menu.
- **directions**: walk; in a menu or a choice, move the cursor one place for every press.

## "The menu"

When a goal says "the menu", "open the menu" or "access the menu" without naming anything else, it means the menu
that the menu button opens: the Dark World menu (state: interact=5). It is not a save point's menu. A save point's
menu is meant only when the goal names the save point.

## The Dark World menu

- Open it with one tap of menu while you can walk. The state then shows interact=5 (the Dark World menu is open)
  and menuno=0 (its top row).
- The top row is a row of icons (ITEM, EQUIP, POWER, CONFIG and so on). Left and right move along it, confirm opens
  the icon under the cursor (menuno becomes 1 to 5: one of its pages), cancel goes back up. Cancel on the top row
  closes the menu: interact goes back to 0 and you can walk again. The state does not say which icon the cursor
  is on, so count your presses.
- An item used from the menu starts a dialogue (interact=6).

## Save points

- A save point is a glowing yellow star; in nearby it is obj_savepoint. Stand next to it (about one tile, 20 px),
  face it and tap confirm once.
- First a short text appears; the program goes through it for you. Then the save point's menu opens (the state
  shows "save point menu"). Its main box has four options: Save (top left), Return (top right), Storage (bottom
  left) and Recruits (bottom right). Directions move the cursor, confirm picks.
- Save shows the three file slots and Return. Confirm on a slot saves to that file (only if this run allows
  saving); Return or cancel goes back to the main box.
- Return, or cancel in the main box, closes the menu, and you can walk again.
- Once you have opened a save point's menu, it stays done: "Done so far" says so. Do not walk back to the star
  to open it again unless the goal asks for that.

## Dialogue and choices

- People and many objects say something when you stand close, face them and tap confirm.
- The program waits for text that is still typing and confirms boxes that wait, so you are asked only when
  something needs a decision.
- A choice is picked with a direction (the choice line says which direction picks which option), then confirm.
- can_move=false: a dialogue, a cutscene or a menu holds you, and walking does nothing. Answer it, close it, or
  wait.

## Walking around

- Rooms join through doors and openings, often at the edge of the room. Walking through one changes the room
  (state: room). The doors are mostly invisible, so they are not in the picture: the exits lines list them for
  the whole room, with the edge each is at and its box. A door at the left or right edge is crossed by lining up
  with its y range and walking toward that edge; one at the top or bottom by lining up with its x range.
- Some ways out have no door object: then walk off the edge where the floor goes on. Paths in the Dark World can
  be dark and hard to see on the screenshot; look along the edges of the room (room_size says how big it is),
  also at the corners and going down or up, not only left and right.
- The exits lines also say where you came in ("you came in here from ..."): that is the way back, so do not take it
  unless the goal is back there.
- Walls and solid objects stop you. If you held a direction and did not move, something is in the way: step
  sideways a little, then try again.
- Walk in short steps (at most 45 frames) while you look for the way, and check the state after each: a long hold
  runs past an opening. If the program warns that you walk back and forth, the way on is not along that line.
- nearby lists only objects with a picture, within 240 px of you, nearest first.

## Playing with another player

- In a game of two or three players, players_pos lists everyone; "you" is the one you play. The camera follows
  player 1 (Kris), so as player 2 or 3 you may be off-centre on the screenshot.
- With shared notes, "Other players" says what the others are doing and what they wrote. Split the search, tell
  them what you find (an exit, a dead end, a puzzle), and go where one of you found the way on.

## Battles

battle=true means a battle started. The program cannot show a battle to you or play it yet. Unless the goal is
about battles, answer "done": true with a "why" that starts with "give up: a battle started".

## Light World and Dark World

The Light World is the ordinary town; the Dark World is the fantasy world. The Dark World menu exists only in the
Dark World. The menu button in the Light World opens a small menu that the state does not describe.
