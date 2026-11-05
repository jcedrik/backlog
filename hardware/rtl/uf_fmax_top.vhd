--------------------------------------------------------------------------------
-- uf_fmax_top: timing harness for uf_decoder_1d, not part of the decoder.
--
-- Puts the decoder behind serial I/O so that place and route fits any N on a
-- small FPGA package: the syndrome shifts in one bit per cycle, and the
-- correction is captured on done and shifts out one bit per cycle. Every
-- decoder output reaches a pin, so synthesis keeps all of its logic. Only
-- used by run_hw.sh to measure Fmax.
--------------------------------------------------------------------------------
library ieee;
use ieee.std_logic_1164.all;
use ieee.numeric_std.all;

entity uf_fmax_top is
  generic (
    N        : positive := 8;
    PARALLEL : boolean  := true
  );
  port (
    clk      : in  std_logic;
    rst      : in  std_logic;
    start    : in  std_logic;
    sin      : in  std_logic;   -- syndrome, one bit per cycle
    sout     : out std_logic;   -- correction, one bit per cycle after done
    busy     : out std_logic;
    done     : out std_logic;
    obs_flip : out std_logic;
    rpar     : out std_logic    -- parity of the rounds counter
  );
end entity uf_fmax_top;

architecture rtl of uf_fmax_top is
  signal syn_sr  : std_logic_vector(N-1 downto 0) := (others => '0');
  signal corr    : std_logic_vector(N downto 0);
  signal corr_sr : std_logic_vector(N downto 0) := (others => '0');
  signal done_i  : std_logic;
  signal rounds  : unsigned(7 downto 0);
begin

  shift : process (clk)
  begin
    if rising_edge(clk) then
      syn_sr <= syn_sr(N-2 downto 0) & sin;
      if done_i = '1' then
        corr_sr <= corr;
      else
        corr_sr <= corr_sr(N-1 downto 0) & '0';
      end if;
    end if;
  end process shift;

  dec : entity work.uf_decoder_1d
    generic map (N => N, PARALLEL => PARALLEL)
    port map (
      clk        => clk,
      rst        => rst,
      start      => start,
      syndrome   => syn_sr,
      busy       => busy,
      done       => done_i,
      correction => corr,
      obs_flip   => obs_flip,
      rounds     => rounds
    );

  done <= done_i;
  sout <= corr_sr(N);
  rpar <= xor rounds;

end architecture rtl;
