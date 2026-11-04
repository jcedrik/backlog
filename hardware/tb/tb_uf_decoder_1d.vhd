--------------------------------------------------------------------------------
-- tb_uf_decoder_1d: self-checking testbench for uf_decoder_1d.
--
-- Reads test vectors produced by the golden model (hardware/model/uf1d_model.py)
-- and checks, for every vector:
--   1. bit-exact match with the golden model: correction, obs_flip, rounds
--   2. independently of the model: the correction reproduces the syndrome
--      (D(i) = c(i) xor c(i+1)). This catches a bug even if model and RTL
--      share the same mistake.
-- Vector line format: <syndrome N bits> <correction N+1 bits> <obs> <rounds>
-- Bits are written MSB first, to match (N-1 downto 0) ordering.
--------------------------------------------------------------------------------
library ieee;
use ieee.std_logic_1164.all;
use ieee.numeric_std.all;
use std.textio.all;

entity tb_uf_decoder_1d is
  generic (
    N       : positive := 8;
    VECTORS : string   := "tb/vectors_n8.txt"
  );
end entity tb_uf_decoder_1d;

architecture sim of tb_uf_decoder_1d is
  constant CLK_PERIOD : time := 10 ns;

  signal clk        : std_logic := '0';
  signal rst        : std_logic := '1';
  signal start      : std_logic := '0';
  signal syndrome   : std_logic_vector(N-1 downto 0) := (others => '0');
  signal busy       : std_logic;
  signal done       : std_logic;
  signal correction : std_logic_vector(N downto 0);
  signal obs_flip   : std_logic;
  signal rounds     : unsigned(7 downto 0);
begin

  clk <= not clk after CLK_PERIOD / 2;

  dut : entity work.uf_decoder_1d
    generic map (N => N)
    port map (
      clk        => clk,
      rst        => rst,
      start      => start,
      syndrome   => syndrome,
      busy       => busy,
      done       => done,
      correction => correction,
      obs_flip   => obs_flip,
      rounds     => rounds
    );

  stim : process
    file     f          : text open read_mode is VECTORS;
    variable ln         : line;
    variable syn_v      : std_logic_vector(N-1 downto 0);
    variable cor_v      : std_logic_vector(N downto 0);
    variable obs_v      : std_logic;
    variable rnd_v      : integer;
    variable n_tests    : natural := 0;
    variable n_mismatch : natural := 0;
    variable n_inv_fail : natural := 0;
    variable max_rounds : natural := 0;
    variable sum_rounds : natural := 0;
    variable max_cycles : natural := 0;
    variable cycles     : natural;
    variable inv_ok     : boolean;
  begin
    rst <= '1';
    for i in 1 to 3 loop
      wait until rising_edge(clk);
    end loop;
    rst <= '0';

    while not endfile(f) loop
      readline(f, ln);
      read(ln, syn_v);
      read(ln, cor_v);
      read(ln, obs_v);
      read(ln, rnd_v);

      -- issue one decode
      wait until rising_edge(clk);
      syndrome <= syn_v;
      start    <= '1';
      wait until rising_edge(clk);
      start    <= '0';

      -- wait for done, counting clock cycles from the start pulse
      cycles := 1;
      while done /= '1' loop
        wait until rising_edge(clk);
        cycles := cycles + 1;
        assert cycles < 1000
          report "timeout waiting for done" severity failure;
      end loop;

      n_tests := n_tests + 1;

      -- 1. bit-exact against the golden model
      if correction /= cor_v or obs_flip /= obs_v
         or to_integer(rounds) /= rnd_v then
        n_mismatch := n_mismatch + 1;
        if n_mismatch <= 10 then
          report "MISMATCH #" & integer'image(n_tests)
               & " syn=" & to_string(syn_v)
               & " corr got=" & to_string(correction)
               & " exp=" & to_string(cor_v)
               & " rounds got=" & integer'image(to_integer(rounds))
               & " exp=" & integer'image(rnd_v)
            severity error;
        end if;
      end if;

      -- 2. model-independent invariant: correction explains the syndrome
      inv_ok := true;
      for i in 0 to N-1 loop
        if (correction(i) xor correction(i+1)) /= syn_v(i) then
          inv_ok := false;
        end if;
      end loop;
      if not inv_ok then
        n_inv_fail := n_inv_fail + 1;
      end if;

      if rnd_v > max_rounds then
        max_rounds := rnd_v;
      end if;
      if cycles > max_cycles then
        max_cycles := cycles;
      end if;
      sum_rounds := sum_rounds + rnd_v;
    end loop;

    report "N=" & integer'image(N)
         & " tests=" & integer'image(n_tests)
         & " mismatches=" & integer'image(n_mismatch)
         & " invariant_failures=" & integer'image(n_inv_fail)
         & " max_rounds=" & integer'image(max_rounds)
         & " mean_rounds_x100=" & integer'image((sum_rounds * 100) / n_tests)
         & " max_cycles_start_to_done=" & integer'image(max_cycles);

    if n_mismatch = 0 and n_inv_fail = 0 and n_tests > 0 then
      report "PASS";
    else
      report "FAIL" severity failure;
    end if;

    std.env.finish;
    wait;
  end process stim;

end architecture sim;
