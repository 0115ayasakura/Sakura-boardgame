"""Paused staged tower sessions and displaced landing regression; no real saves."""
from pathlib import Path
import sys,copy
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'sakura_boardgame'))
import engine
from engine import MonopolyGame,GameError,restore_game
class Dice:
 def __init__(self,n=1):self.n=n;self.calls=0
 def randint(self,a,b):self.calls+=1;return self.n
 def randrange(self,n):return 0
def game():return MonopolyGame(size=28,dice_sides=6,seed=5,map_kind='grid')
def rejects(fn):
 try:fn()
 except GameError:return
 raise AssertionError('Expected paused/invalid action rejection')
def enter(g,actor='user'):
 gate=next(c['id'] for c in g.cells if c['type']=='tower');g.positions[actor]=gate;g.turn=actor;g._enter_device(actor,gate,[]);return gate
def test_staged_pause_and_third_floor_rng():
 g=game();d=Dice(6);g._effect_rng=lambda:d;gate=enter(g);cash=dict(g.cash);seq=g._tower['seq']
 assert g.pending['type']=='tower' and g._tower['at']==-1
 assert g.cash==cash and g._last_landing is None and d.calls==0
 rejects(lambda:g.roll('sakura'));rejects(lambda:g.roll('user'));rejects(lambda:g.advance_tower('user',seq,-1,True))
 for expected in (-1,0,1):
  g.advance_tower('user',seq,expected)
  assert g.turn=='user' and g.pending['type']=='tower'
  assert [r['resolved'] for r in g._tower['floors']]==[i<=expected+1 for i in range(3)]
  if expected<1:assert g._last_landing is None and d.calls==0
 assert d.calls==1 and g._last_landing['towerFloor']==2
 assert g.positions['user']==gate
 g.advance_tower('user',seq,2,True);assert g.turn=='sakura' and not g.pending and not g._tower['active']
 state=g.to_dict();g.advance_tower('user',seq,2,True);assert g.to_dict()==state

def test_duplicate_and_out_of_order_requests():
 g=game();enter(g);seq=g._tower['seq'];rejects(lambda:g.advance_tower('user',seq,1));rejects(lambda:g.advance_tower('sakura',seq,-1));rejects(lambda:g.advance_tower('user',seq+1,-1))
 g.advance_tower('user',seq,-1);saved=g.to_dict();result=g.advance_tower('user',seq,-1);assert result['duplicate'] and g.to_dict()==saved

def test_restore_active_tower():
 g=game();enter(g);seq=g._tower['seq'];g.advance_tower('user',seq,-1);restored=restore_game(g.to_dict())
 assert restored._tower['at']==0 and restored.pending['type']=='tower'
 restored.advance_tower('user',seq,0);assert restored._tower['at']==1

def test_sakura_also_pauses():
 g=game();enter(g,'sakura');seq=g._tower['seq'];rejects(lambda:g.roll('user'))
 for expected in (-1,0,1):g.advance_tower('sakura',seq,expected)
 assert g.turn=='sakura';g.advance_tower('sakura',seq,2,True);assert g.turn=='user'

def test_movement_card_deferred_until_exit():
 g=game();gate=enter(g);card={'title':'move','effect':{'type':'goto_start'}}
 g._apply_card('user',card,'',[]);assert g.positions['user']==gate and len(g._tower['deferred'])==1
 for expected in (-1,0,1):g.advance_tower('user',g._tower['seq'],expected)
 g.advance_tower('user',g._tower['seq'],2,True);assert g.positions['user']==g.start_node

def displaced(kind,actor='user'):
 g=game();here,ahead=g.main_path[1:3];other=g._other(actor);g.cells[here]['type']=kind;g.cells[ahead]['type']='coin';g.positions[actor]=here;g.positions[other]=ahead;g.turn=actor;g.last_step[actor]=None;g._dice_rng=lambda:Dice(1);return g,here,ahead,other

def test_displaced_coin_settles_real_cell():
 for actor in ('user','sakura'):
  g,here,ahead,other=displaced('coin',actor);before=g.cash[actor];r=g.roll(actor)
  assert r['path']==[here,ahead,here] and g.positions[actor]==here and g.cash[actor]>before
  assert g._last_walk['blocked'] and g.last_step[actor] is None and g.turn==other

def test_displaced_property_decision_and_rent():
 g,here,ahead,other=displaced('property');g.cells[here].update(owner=None,price=60,tier=1,group='daily');g.roll('user')
 assert g.pending['type']=='land' and g.pending['cell']==here and g.turn=='user';g.decide('user','skip');assert g.turn==other
 g,here,ahead,other=displaced('property');g.cells[here].update(owner=other,price=60,tier=1,group='daily');before=g.cash['user'];g.roll('user');assert g.cash['user']<before

def test_displaced_tower_and_game():
 g,here,ahead,other=displaced('tower');g.roll('user');assert g.pending['type']=='tower' and g._tower['cell']==here and g._last_landing is None
 g,here,ahead,other=displaced('game');g.roll('user');assert g._last_landing['cell']==here

if __name__=='__main__':
 tests=[(n,f) for n,f in list(globals().items()) if n.startswith('test_') and callable(f)]
 for name,test in tests:test();print('PASS',name)
 print('Tower/yield regression passed',len(tests))
